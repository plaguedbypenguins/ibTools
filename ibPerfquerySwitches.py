#!/usr/bin/env python

# (c) Robin Humble 2010-2012
# licensed under the GPL v3

# get IB switch traffic stats for each type of switch layer (m2, lc, fc, qnem) by querying all switch ports.
# produces ganglia entries of eg.
#   {m2,fc,lc,qnem}_ib_bytes  in bytes/sec
#   {m2,fc,lc,qnem}_ib_pkts   in packets/sec
#
#  - at startup slurp the latest ibnetdiscover output to figure out which are switch ports
#  - run perfqueryMany commands every so often
#  - compute the rates (ignoring over/under flows)
#  - sum the rates for each switch type
#  - pound that info into ganglia via gmetric spoofing
#  - every so often check for a new ibnetdiscover

import sys
import time
import subprocess
import socket

from ibTracePorts import parseIbnetdiscover
from ibCheckTopology import findLidsByType
from compact import compressList

# sleep this many seconds between samples
sleepTime = 15

# check for a new topology file every so many seconds
topologyFileCheckInterval = 3600

chipTypes = ( 'M2', 'qnem', 'LC', 'FC' )

# limit insane data
dataMax = 3*1024*1024*1024   # 3GB/s
pktsMax = 10*1024*1024  # 3 M pkts/s is too low, try 10

host='vu-pbs'
ipCache = {}

def runIbperfCommand( cmd ):
   p = subprocess.Popen( cmd, shell=True, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE )
   out, err = p.communicate()
   r = out.split('\n')
   # write any errs to stderr
   if len(err):
      sys.stderr.write( sys.argv[0] + ': Error: runIbperfCommand: ' +  err )
   return r

def getIp(host):
   try:
      ip = ipCache[host]
   except:
      #print 'host', host, 'not in ipCache'
      ip = socket.gethostbyname(host)
      ipCache[host] = ip
   return ip

def buildIbCmd( switchTree, keys ):
   cmd = '/opt/root/perfqueryMany'
   cnt = 0
   lp = []
   for k in keys:
      swName, swLid, a = switchTree[k]
      for p in a.keys():
         #print 'lid, port', k, p
         cmd += ' %d %d' % ( k, p )
         lp.append( (k, p) )
         cnt += 1
   return cmd, cnt, lp

def compactPairs( a ):
   l = []
   ll = []
   last = None
   for i, j in a:
      if last == None:
         last = i
         l.append(j)
      elif i == last:
         l.append(j)
      else:
         l = compressList(l)
         ll.append( (last, l) )
         last = i
         l = []
         l.append(j)
   if len(l):
      l = compressList(l)
      ll.append( (last, l) )
   return ll

def parseToStats( r, lp ):
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


   # see what we have managed to read from the chips
   lpData = []
   for i in r:
      if i[:6] == '# Port':
         ii = i.split(':')[1]
         ii = ii.split()
         key = ( int(ii[1]), int(ii[3]) )
         lpData.append( key )

   if lpData == lp:
       # all is good
       pass
   elif len(lp) < len(lpData):
       sys.stderr.write( sys.argv[0] + ': Error: got too much switch lid/port data back: asked for %d, got %d. scary. skipping it all.\n' % ( len(lp), len(lpData) ) )
       return {}
   else:
       # the most common error case is that a query or several failed.
       # so handle what we were given and report the rest as errors
       errs = []
       for k in lp:
          if k not in lpData:
             errs.append(k)
       sys.stderr.write( sys.argv[0] + ': Error: expected %d responses, got %d.' % (len(lp), len(lpData) ) + ' Lid/Ports missing: ' + str(compactPairs(errs)) + '\n' )

       # be paranoid and check the other way too...
       errs = []
       for k in lpData:
          if k not in lp:
             errs.append(k)
       if len(errs):
          sys.stderr.write( sys.argv[0] + ': Error: too many Lid/Ports returned in data. skipping it all\n' )
          return {}

   reading = 0
   s = {}
   d = None
   for i in r:
      if i[:6] == '# Port':
         ii = i.split(':')[1]
         ii = ii.split()
         key = ( int(ii[1]), int(ii[3]) )
         reading = 1
         d = []
      elif i[:9] == 'timestamp':
         reading = 0
         t = float(i.split()[1])
         #print h, 'time', t
         if len(d) != 4:
            sys.stderr.write( sys.argv[0] + ': Error: skipping timestamp: did not find 4 ib stats\n' )
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

   return s
 
def computeRates( sOld, s ):
   rates = {}
   weirdCnt = 0
   weirdThresh = 5
   # host, ( time, [txData, rxData, txPkts, rxPkts] )
   for h in s.keys():
      if h in sOld.keys():
         t, d = s[h]
         tOld, dOld = sOld[h]
         dt = t - tOld
         r = []
         for i in range(len(d)):
            if dt <= 0.0:
               bad = 1
            else:
               dd = float(d[i] - dOld[i])/dt
               r.append(dd)
         if r[0] > dataMax or r[0] < 0:
            if weirdCnt < weirdThresh:
               print 'trapped weird txData', r[0], h
            weirdCnt += 1
            r[0] = 0.0
         if r[1] > dataMax or r[1] < 0:
            if weirdCnt < weirdThresh:
               print 'trapped weird rxData', r[1], h
            weirdCnt += 1
            r[1] = 0.0
         if r[2] > pktsMax or r[2] < 0:
            if weirdCnt < weirdThresh:
               print 'trapped weird txPkts', r[2], h
            weirdCnt += 1
            r[2] = 0.0
         if r[3] > pktsMax or r[3] < 0:
            if weirdCnt < weirdThresh:
               print 'trapped weird rxPkts', r[3], h
            weirdCnt += 1
            r[3] = 0.0

         rates[h] = r

   if weirdCnt >= weirdThresh:
      print 'trapped many weird pkts/data - cnt', weirdCnt

   return rates

def parseValsToGmetricLines(rate, t, spoofStr):
   # units of Data are "octets divided by 4", which means bytes/4, so 1 unit is 4 bytes.
   txData = rate[0]*4.0
   rxData = rate[1]*4.0
   txPkts = rate[2]
   rxPkts = rate[3]

   c = []

   # switches can't be sinks or sources so tx == rx, but average them anyway...
   c.append( '/usr/bin/gmetric -S ' + spoofStr + ' -t float -n "' + t + '_ib_bytes" -u "bytes/sec"   -v %.2f\n' % (0.5*(txData + rxData)) )
   c.append( '/usr/bin/gmetric -S ' + spoofStr + ' -t float -n "' + t + '_ib_pkts"   -u "packets/sec" -v %.2f\n' % (0.5*(txPkts + rxPkts)) )

   return c


def sumRates(rates):
   keys = rates.keys()
   tot = [0.0, 0.0, 0.0, 0.0]
   for k in keys:
      for i in range(4):
         tot[i] += rates[k][i]
   return tot


def writeToGanglia(c):
   # pump vals into ganglia via gmetric
   p = subprocess.Popen( '/bin/sh', shell=False, bufsize=-1, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE )
   for i in c:
      p.stdin.write( i )
   out, err = p.communicate()

   # ignore gmetric's spoof info line, send the rest to stderr
   for o in out.split('\n'):
      i = o.split()
      if len(i) and i[0].strip() != 'spoofName:':
         sys.stderr.write( sys.argv[0] + ': Error: gmetric stdout:' + str(i) + '\n' )

   # print err if any
   if len(err):
      sys.stderr.write( sys.argv[0] + ': Error: gmetric stderr: ' +  str(err) + '\n' )

def findTopology():
   #sys.stderr.write( sys.argv[0] + ': Info: reading ibnetdiscover file.\n' )
   switchTree, blah, lidPortHost, blah = parseIbnetdiscover()
   #print 'switchTree (len', len(switchTree), ')' # , switchTree  # by switch LID

   s = {}
   sOld = {}
   keys = {}
   cmd = {}
   lp = {}
   for t in chipTypes:
      s[t] = {}
      sOld[t] = {}
      keys[t] = findLidsByType( switchTree, t )
      cmd[t], cnt, lp[t] = buildIbCmd( switchTree, keys[t] )
      #print 'cmd', t, cmd[t]
      #print 'lp', t, lp[t]
      #for l,p in lp[t]:
      #   print t,l,switchTree[l][0],p
   #sys.exit(1)
   return s, sOld, cmd, lp

def topologyChanged( cmd, lp ):
   # read the latest ibnetdiscover file
   blah, blah, cmdNew, lpNew = findTopology()
   if cmdNew == cmd and lpNew == lp:
      return (0, ())  # unchanged

   # be paranoid and read it again 60s later in case the file is just being written
   sys.stderr.write( sys.argv[0] + ': Info: found changed ibnetdiscover file. waiting 60s and reading again\n' )
   time.sleep(60)

   s, sOld, cmdNew2, lpNew2 = findTopology()
   if cmdNew2 == cmd and lpNew2 == lp:
      return (0, ())  # was unchanged after all
   if cmdNew != cmdNew2 or lpNew != lpNew2:
      return (0, ())  # something is changing/odd. don't trust the new file

   sys.stderr.write( sys.argv[0] + ': Info: using new ibnetdiscover file.\n' )

   # changed
   return (1, (s, sOld, cmdNew, lpNew))

if __name__ == '__main__':
   first = 1
   s, sOld, cmd, lp = findTopology()
   topologyCheckTime = time.time()

   ip = getIp(host)
   if ip == None:
      print 'cannot lookup', host
      sys.exit(1)
   spoofStr = ip + ':' + host
   #print 'spoofStr', spoofStr

   while 1:
      if not first:
         time.sleep(sleepTime)

      if time.time() - topologyCheckTime > topologyFileCheckInterval:
         topologyCheckTime = time.time()
         changed, newTopo = topologyChanged( cmd, lp )
         if changed:
            first = 1
            s, sOld, cmd, lp = newTopo

      for t in chipTypes:
         r = runIbperfCommand( cmd[t] )
         #print 't', t, 'r', r

         s[t] = parseToStats( r, lp[t] )
         #print 't', t, 's', s[t]

         rates = computeRates( sOld[t], s[t] )
         #print 't', t, 'rates', rates
         sOld[t] = s[t]

         if first:
            continue

         rate = sumRates(rates)
         #print 't', t, 'rate', rate
         #sys.exit(1)

         c = parseValsToGmetricLines(rate, t.lower(), spoofStr)
         if not len(c):
            print 'error making gmetric lines', t
            continue
         #print 't', t, 'c', c

         writeToGanglia(c)

      first = 0
