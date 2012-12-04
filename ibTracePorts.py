#!/usr/bin/env python

# (c) Robin Humble 2010
# licensed under the GPL v3

# take an IB 'directed path address', and use ibnetdiscover output to figure out the endpoint, usually a host.

#  - slurp up a designated ibnetdiscover output to figure out
#    which switch ports are connected to HCA's

import sys
import socket
import os

ibDirDefault = '/root/ib'
startHost='xepbs'
startHost='vu-pbs'
startHost='r-pbs'

# dirty hack for as yet un-named switches
hackMapping = { 'S-0002c903008bc480':'ib301',
                'S-0002c903008bca80':'ib302',
                'S-0002c903008bc580':'ib303',
                'S-0002c903008be700':'ib304',
                'S-0002c903008be780':'ib305',
                'S-0002c903008be300':'ib306',
                'S-0002c903008bde00':'ib307',
                'S-0002c903008bdf80':'ib308',
                'S-0002c903008c0f00':'ib039',
                'S-0002c903008c6380':'ib045' }

def parseIbnetdiscover( ibDir=None, ibNetFile=None ):
   f = ibNetFile
   d = ibDir
   if d == None:
      d = ibDirDefault
   if f == None:
      suffix = 'ibnetdiscover'
      f, fTime = findMostRecentFile( d, suffix )
   print 'using', f, 'in dir', d
   f = d + '/' + f
   lines = open( f, 'r' ).readlines()
   #print lines

   # ...
   # Switch  36 "S-0021283a8836a0a0"         # "0x0021283a8836a0a0 M2-1" enhanced port 0 lid 1678 lmc 0
   # [36]    "H-00212800013e555a"[1](212800013e555b)                 # "marmot1 HCA-1" lid 91 4xQDR
   # [35]    "S-0021283a89e015d2"[34]                # "0x0021283a89e015d2 M9-3-LC-5d" lid 520 4xQDR
   # [34]    "H-00212800013e5822"[1](212800013e5823)                 # "marmot3 HCA-1" lid 32 4xQDR
   # [33]    "S-0021283a89e015d2"[33]                # "0x0021283a89e015d2 M9-3-LC-5d" lid 520 4xQDR
   # ...

   # look for line pairs like ->
   # Ca      2 "H-00212800013e60f6"          # "marmot2 HCA-1"
   # [1](212800013e60f7)     "S-0021283a842110d2"[28]                # lid 241 lmc 0 "Sun DCS 648 QDR LC switch 1.6" lid 211 4xQDR

   lph = []
   switchTree = {}
   byName = {}
   rates = {}
   d = None
   next = 0
   for l in lines:
      if next == 'ca':
         swlid = int(l.split()[-2])
         swport = int(l.split('[')[2].split(']')[0])
         lid = int(l.split('#')[1].split()[1])
         lph.append( ( swlid, swport, lid, host ) )
         rates[(lid, 1)] = l.split()[-1]
         next = 0
      elif l[:2] == 'Ca':
         host = l.split('"')[3]
         h = host.split()
         if len(h) > 1:
            if h[1] == 'HCA-1' or h[1] == 'HCA-2':
                host = host.split()[0]
         else:
            #print 'skipping unnamed node', l,
            next = 0
            continue
         #print host
         next = 'ca'
      elif l[:6] == 'Switch':
         s = l.split('"')
         if len(s[3].split()) > 1:
            swName = s[3].split()[1]
         else:
            swName = s[3]
         if swName == '' or swName == '-':
            # hack hack hack - the names should be in the fabric or node map files, but in case they aren't...
            if s[1] in hackMapping.keys():
               print 'using hackMapping for', s[1], 'to', hackMapping[s[1]]
               swName = hackMapping[s[1]]
            else:
               print 'error. unnamed switch chip', s
         swLid = int(s[4].split()[4])
         #print 'sw', swName, 'lid', swLid
         d = {}
         next = 'ports'
      elif next == 'ports':
         s = l.split('"')
         if len(s) < 2:
            next = 0
            switchTree[swLid] = [ swName, swLid, d ]
            continue
         # down this switch port number...
         port = int(s[0].split(']')[0][1:])
         # ... we have this lid for a host/switch
         lid = int(s[4].split()[1])
         # ... which talks to us on this port
         remPort = int(s[2].split(']')[0][1:])
         t = s[1][0]
         if t == 'H':    # host at the end of this port
            name = s[3].split()[0]
         elif t == 'S':  # switch  ""
            if len(s[3].split()) > 1:
               name = s[3].split()[1]
            else:
               name = s[3]
         else:
            print 'unknown type of link from switch. line is', l
            continue
         d[port] = [ name, lid, remPort ]
         if t == 'H':
            byName[name] = [ port, swName, swLid ]
         #print 'port', port, 't', t, 'lid', lid, 'name', name
         rates[(swLid, port)] = l.split()[-1]

   return switchTree, byName, lph, rates


def findMostRecentFile( d, suffix ):
   l, lt, lp, ltp = findMostRecentFiles( d, suffix )
   return l, lt

def findMostRecentFiles( d, suffix ):
   files = os.listdir( d )
   last = None
   lastTime = 0
   lastPrev = None
   lastTimePrev = 0
   for f in files:
      if f.split('.')[-1] == suffix:
         m = os.path.getmtime( d + '/' + f)
         if m > lastTime:
            lastPrev = last
            lastTimePrev = lastTime
            last = f
            lastTime = m
         elif m > lastTimePrev:
            lastPrev = f
            lastTimePrev = m

   return last, lastTime, lastPrev, lastTimePrev

if __name__ == '__main__':

   ibNetFile = None
   if len(sys.argv) == 3:
      ibNetFile = sys.argv[2]
      path = sys.argv[1]
   elif len(sys.argv) == 2:
      path = sys.argv[1]
   else:
      print 'usage', sys.argv[0], '<path> [ibnetdiscover file]'
      print 'eg.', sys.argv[0], '0,1,31,18,33,31,27  /root/ib/2010-04-07-15:02:29.ibnetdiscover'
      sys.exit(1)

   switchTree, byName, lph, r = parseIbnetdiscover(ibNetFile=ibNetFile)
   #print 'switchTree (len', len(switchTree), ')', switchTree
   #print 'byName (len', len(byName), ')', byName

   name = socket.gethostname()
   if name != startHost:
      print 'WARNING - the port trace is assumed to be relative to', startHost, 'not this host', name
      #sys.exit(1)
      name = startHost
   start = byName[name]
   port, swName, swLid = start
   print 'start at', name, 'lid', swLid, 'attached to switch', swName, 'port', port
   loc = swLid

   # eg.  1,31,1,33,33    <- a dead link
   # or
   # 0,1,31,18,33,31,27  -> host == v1224
   #    0,1 is out of vupbs
   #    31 port out of qnem
   #    18 port out of LC
   #    33         FC
   #    31         LC
   #    27         qnem

   # pull off initial '0,' if there is one
   if path[:2] == '0,':
      path = path[2:]
   print 'path', path

   if path[:2] != '1,':
      print 'we assume path always starts with "1,". need to fix this script if you want something else.'
      sys.exit(1)
   else:
      path = path[2:]

   name = swName
   path = path.split(',')
   for p in path:
      p = int(p)
      swName, swLid, d = switchTree[loc]
      nextHop = d[p]
      #print 'nextHop', nextHop
      name, loc, remPort = nextHop
      print '... travelling to port', p, '(%s, lid %d)' % ( name, loc )

   print 'destination', name, 'lid', loc, 'port', remPort
