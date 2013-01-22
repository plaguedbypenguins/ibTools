#!/usr/bin/env python

# (c) Robin Humble 2010
# licensed under the GPL v3

# get IB network stats from all IB hardware and dump to a file.

import sys
import time
import string
import subprocess
import socket
import os
import cPickle
import getopt

from ibTracePorts import parseIbnetdiscover

dumpFile = None

ibDir = '/home/rjh'
ibDir = '/root/ib'

portsAtOnce = 1000
perfCmd = '/opt/root/perfqueryMany'

def runChunkedIbperfs( lpn ):
   chunks = len(lpn)/portsAtOnce
   if chunks * portsAtOnce < len(lpn):
      chunks += 1

   r = []
   s = {}
   for c in range(chunks):
      cmd = perfCmd

      start = c*portsAtOnce
      end = min((c+1)*portsAtOnce, len(lpn))
      #print 'start, end', start, end
      for i in range(start,end):
         lid, port, name = lpn[i]
         #print 'i', i, 'lpn', lpn[i]
         cmd += ' %d %d' % ( lid, port )

      r = runIbperfCommand( cmd )
      #print 'r', r
      parseToStats( s, r, lpn, start )

   return s

def runIbperfCommand( cmd ):
   p = subprocess.Popen( cmd, shell=True, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE )
   out, err = p.communicate()
   r = out.split('\n')
   # write any errs to stderr
   if len(err):
      sys.stderr.write( sys.argv[0] + ': Error: runIbperfCommand: ' +  err )
   return r

def lidPorts( lidPortHost, switchTree ):
   lpn = []

   hCnt = 0
   for swl,swp,l,h in lidPortHost:
      lpn.append( ( l, 1, h ) )
      hCnt += 1

   swCnt = 0
   for l in switchTree.keys():
      swName, swLid, ports = switchTree[l]
      for p in ports.keys():
         lpn.append( ( swLid, p, swName ) )
         swCnt += 1

   return lpn, hCnt, swCnt

def parseToStats( s, r, lpn, start ):
   # expect this ->

   # # Port counters: Lid 214 port 35
   # PortSelect:......................35
   # CounterSelect:...................0x1b01
   # PortXmitData:....................196508288843
   # PortRcvData:.....................550793773496
   # PortXmitPkts:....................6618992385
   # PortRcvPkts:.....................2242117286
   # PortUnicastXmitPkts:.............5312308102
   # PortUnicastRcvPkts:..............3582819080
   # PortMulticastXmitPkts:...........3020372621
   # PortMulticastRcvPkts:............226383425
   # timestamp 1264494583.110014

   # or (later) with an [extended] in the 1st line ->

   # # Port extended counters: Lid 61 port 35
   # PortSelect:......................35
   # CounterSelect:...................0x1b01
   # PortXmitData:....................2865699555629
   # PortRcvData:.....................5818573238645
   # PortXmitPkts:....................8450135651
   # PortRcvPkts:.....................15372362298
   # PortUnicastXmitPkts:.............8450138387
   # PortUnicastRcvPkts:..............15372365034
   # PortMulticastXmitPkts:...........0
   # PortMulticastRcvPkts:............0
   # timestamp 1279194662.416459

   # sometimes with a prefix of this
   # ibwarn: [21453] main: PerfMgt ClassPortInfo 0x400 extended counters not indicated

   # but should be able to handle

   # <some errors>
   # timestamp 1264494583.110014

   cnt = start
   reading = 0
   d = None
   for i in r:
      if i[:6] == '# Port':
         ii = i.split(':')[1]
         ii = ii.split()
         looping = 1
         # hosts with 32bit counters fail, so need to have this loop to skip them
         while looping:
            key = lpn[cnt]
            ( lid, port, name ) = key
            cnt += 1
            # check it's the next lid/port we're expecting
            if int(ii[1]) != lid or int(ii[3]) != port:
               sys.stderr.write( sys.argv[0] + ': Error: expected lid/port %d/%d' % ( lid, port ) + ' (' + name + ') not ' + i + '\n' )
            else:
               looping = 0
         reading = 1
         d = []
      elif i[:9] == 'timestamp':
         reading = 0
         t = float(i.split()[1])
         #print h, 'time', t
         if len(d) != 4:
            sys.stderr.write( sys.argv[0] + ': Error: skipping ' + name + ': did not find 4 ib stats\n' )
            continue
         s[key] = ( t, d )
      else:
         if not reading:
            continue
         ii = i.split(':')
         if ii[0] in ( 'PortXmitData', 'PortRcvData', 'PortXmitPkts', 'PortRcvPkts' ):
            val = ii[1].strip('.')
            #print h, ii[0], val
            d.append( int(val) )


def dumpStats( s ):
   f = open( dumpFile, 'w+b' )
   cPickle.dump( s, f )
   f.close()

def usage():
   print 'usage:', sys.argv[0], '[-h|--help] dumpFileName'
   sys.exit(1)

def parseArgs():
   global dumpFile

   try:
      opts, args = getopt.getopt( sys.argv[1:], 'h', ['help' ] )
   except getopt.GetoptError:
      usage()  # print help information and exit

   for o, a in opts:
      if o in ('-h', '--help'):
         usage()
   if len(args) != 1:
      usage()

   dumpFile = sys.argv[1]

if __name__ == '__main__':
   parseArgs()

   switchTree, byName, lidPortHost, r = parseIbnetdiscover( ibDir=ibDir )
   #print 'switchTree (len', len(switchTree), ')' # , switchTree  # by switch LID
   #print 'byName (len', len(byName), ')' # , byName  # by hostname
   #print 'lph (len', len(lidPortHost), ')' # , lidPortHost  # swlid, swport, lid, host

   lpn, hCnt, swCnt = lidPorts( lidPortHost, switchTree )
   #print 'hCnt', hCnt, 'swCnt', swCnt, 'len(lpn)', len(lpn) #, 'lpn', lpn

   s = runChunkedIbperfs( lpn )
   dumpStats( s )
