#!/usr/bin/env python

# (c) Robin Humble 2010-2012
# licensed under the GPL v3

# get IB network stats from IB hardware and spoof them into ganglia.
# this is centralised because then no daemon needs to run on compute
# nodes.
#
# with --host aquires data from host HCA's (requires 64bit stats on hosts - so connectx fw >= 2.7.0)
# with --switch will find which switch ports each host is plugged into and will reverse map the traffic

#  - at startup slurp the latest ibnetdiscover output to figure out
#    which switch ports are connected to HCA's
#  - run perfqueryMany commands every so often
#  - compute the rates (ignoring over/under flows)
#  - pound that info into ganglia via gmetric spoofing
#  - if it looks like new hosts have been added to the fabric, then re-run ibnetdiscover

import sys
import time
import string
import subprocess
import socket
import os

from pbsMauiGanglia import gangliaStats
from ibTracePorts import parseIbnetdiscover
from bobMonitor import pbsJobsBob  # to find which jobs are on a flickering node

# only gather from nodes that we've heard from in the last 'aliveTime' seconds
aliveTime = 120

# sleep this many seconds between samples
#  NOTE - needs to be > aliveTime to avoid nodes 'alive' with our own spoof'd data
#    .... fuckit... do it often ... sigh - fix ganglia spoofing later.
sleepTime = 15

# --host or --switch mode
hostMode = 'host'

# limit insane data
dataMax = 4*1024*1024*1024   # 4GB/s
pktsMax = 10*1024*1024  # 3 M pkts/s is too low, try 10

# unreliable hosts
unreliable = []
# no networking on cmms
for f in range(1,65):
   unreliable.append( 'cmm%d' % f )
# remove gige
for f in range(1,27):
   unreliable.append( 'hamster%d' % f + 'gige' )
for s in [ 'vu-man', 'gopher', 'vayu' ]:
   for f in range(1,5):
      unreliable.append( s + '%d' % f + 'gige' )
#unreliable.append( "gopher4" )
unreliable.append( "lolza" )
unreliable.append( "gopher4" )
#unreliable.append( "vu-man3" )
unreliable.append( "roffle" )
unreliable.append( "rofflegige" )

## router nodes
#useAnyway = [ 'knet00', 'knet01' ]

crashedOs = []
ipCache = {}

def findUpDown(all, timeout):
    now = time.time()  # seconds since 1970
    up = []
    down = []
    for host in all.keys():
        if now - all[host]['reported'] < timeout:
             up.append(host)
        else:
             down.append(host)
    return up, down

def listOfUpHosts(deadTimeout):
    g = gangliaStats( reportTimeOnly=1 )
    all = g.getAll()

    up, down = findUpDown(all, deadTimeout)
    up.sort()
    #print 'down', down
    #print 'up', up

    # delete hosts with unreliable bmc's
    for u in unreliable:
        if u in up:
            #print 'deleting unreliable', u
            up.remove(u)
    #sys.exit(1)

    return up

def runCommand( cmd ):
   p = subprocess.Popen( cmd, shell=True, bufsize=-1, stdout=subprocess.PIPE, stderr=subprocess.PIPE )
   out, err = p.communicate()
   # write any errs to stderr
   if len(err):
      sys.stderr.write( sys.argv[0] + ': Error: runCommand: ' + cmd[0] + ' ... ' +  err )
   return out, err

def getIp(host):
   try:
      ip = ipCache[host]
   except:
      #print 'host', host, 'not in ipCache'
      ip = socket.gethostbyname(host)
      ipCache[host] = ip
   return ip

def compareIbToGanglia( lidPortHost, up ):
   ibHosts = []
   for swl,swp,l,h in lidPortHost:
      ibHosts.append(h)
   newlyDown = []
   for h in ibHosts:
      #if h in useAnyway:  # assume always up, even if not seen anywhere else
      #   if h not in up:
      #      up.append(h)
      #   continue
      if h not in up and h not in unreliable:
         if h not in crashedOs:
            crashedOs.append(h)
            newlyDown.append(h)  # print msg about this below
            sys.stderr.write( sys.argv[0] + ': Warning: ' + h + ' on ib but not in ganglia (will only print this once)\n' )
      else:
         if h in crashedOs:
            crashedOs.remove(h)
            sys.stderr.write( sys.argv[0] + ': Info: ' + h + ' in ib. was out of ganglia, but now back up\n' )

   #newlyDown = [ "v1205", "v1206" ]
   # check for multiple down nodes running the same job
   if len(newlyDown):
      p = pbsJobsBob()
      jobs = p.getJobList()
      j = {}
      for h in newlyDown:
         for username, nodeList, line, tagId, timeToGo, jobId, jobName, pbsInfo in jobs:  # append to joblist field
            if h in nodeList:
               if 'state' in pbsInfo.keys() and pbsInfo['state'] != 'S':
                  k = str( ( username, line ) )
                  if k not in j.keys():
                     j[k] = []
                  j[k].append(h)
      for h in j.keys():
         if len(j[h]) > 1: # more than 1 node of this job is down
            sys.stderr.write( sys.argv[0] + ': Warning: job ' + str(h) + ' has multiple nodes down in ganglia ' + str(j[h]) + '\n' )
   #sys.exit(1)

   newhosts = 0
   for h in up:
      if h not in ibHosts:
         sys.stderr.write( sys.argv[0] + ': Error: ' + h + ' up in ganglia but not on ib\n' )
         newhosts = 1
   #sys.exit(1)

   return newhosts

def runIbnetdiscover():
   # run this:
   #   /usr/sbin/ibnetdiscover > /root/ib/`date +'%F-%T'`.ibnetdiscover
   sys.stderr.write( sys.argv[0] + ': Info: running ibnetdiscover\n' )
   r, err = runCommand( '/usr/sbin/ibnetdiscover' )
   if len(err) != 0:
      sys.stderr.write( sys.argv[0] + ': Error: running ibnetdiscover failed\n' )
      return 1
   fn = '/root/ib/' + time.strftime('%Y-%m-%d-%H:%M:%S' ) + '.ibnetdiscover'
   try:
      f = open(fn, 'w')
   except:
      sys.stderr.write( sys.argv[0] + ': Error: open of file ' + fn + ' failed\n' )
      return 1
   try:
      f.writelines(r)
   except:
      sys.stderr.write( sys.argv[0] + ': Error: write to file ' + fn + ' failed\n' )
      f.close()
      return 1
   f.close()
   return 0

def buildIbCmd( lidPortHost, up ):
   lp = '/opt/root/perfqueryMany'
   cnt = 0
   for swl,swp,l,h in lidPortHost:
      if h in up:   # only gather for up hosts
         if hostMode == 'host':
            lp += ' %d %d' % ( l, 1 )
         else:
            lp += ' %d %d' % ( swl, swp )
         cnt += 1
   return lp, cnt

def parseToStats( r, lidPortHost, up ):
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

   upTo = 0
   reading = 0
   s = {}
   d = None
   errCnt = 0
   for i in r:
      if i[:6] == '# Port':
         # check it's the next lid/port we're expecting
         h = ''
         while h not in up:
            swlid, swport, lid, h = lidPortHost[upTo]
            upTo += 1
         ii = i.split(':')[1]
         ii = ii.split()
         #print 'h', h, 'i', i
         if hostMode == 'switch' and ( int(ii[1]) != swlid or int(ii[3]) != swport ):
            if errCnt < 1:
               sys.stderr.write( sys.argv[0] + ': Error: host ' + h + ': expected switch lid/port %d/%d' % ( swlid, swport ) + ' not ' + i + '. Supressing further errors\n' )
            errCnt += 1
            continue
         elif hostMode == 'host' and ( int(ii[1]) != lid or int(ii[3]) != 1 ):
            if errCnt < 1:
               sys.stderr.write( sys.argv[0] + ': Error: host ' + h +': expected host lid/port %d/%d' % ( lid, 1 ) + ' not ' + i + '. Supressing further errors\n' )
            errCnt += 1
            continue
         reading = 1
         d = []
      elif i[:9] == 'timestamp':
         reading = 0
         t = float(i.split()[1])
         #print h, 'time', t
         if len(d) != 4:
            sys.stderr.write( sys.argv[0] + ': Error: skipping ' + h + ': did not find 4 ib stats\n' )
            continue
         s[h] = ( t, d )
      else:
         if not reading:
            continue
         ii = i.split(':')
         if ii[0] in ( 'PortXmitData', 'PortRcvData', 'PortXmitPkts', 'PortRcvPkts' ):
            val = ii[1].strip('.')
            #print h, ii[0], val
            d.append( int(val) )

   if upTo != len(lidPortHost):
      sys.stderr.write( sys.argv[0] + ': Error: expected %d responses and got %d. ErrCnt %d\n' % (len(lidPortHost), upTo, errCnt ) )
   elif errCnt:
      sys.stderr.write( sys.argv[0] + ': Error: ErrCnt %d\n' % ( errCnt ) )

   return s

def computeRates( sOld, s ):
   rates = {}
   # host, ( time, [txData, rxData, txPkts, rxPkts] )
   for h in s.keys():
      if h in sOld.keys():
         t, d = s[h]
         tOld, dOld = sOld[h]
         dt = t - tOld
         bad = 0
         r = []
         for i in range(len(d)):
            if dt <= 0.0:
               bad = 1
            else:
               dd = float(d[i] - dOld[i])/dt
               r.append(dd)
            if dd < 0.0:
               bad = 1
         if not bad:
            rates[h] = r
   return rates

def parseValsToGmetricLines(rates, up):
   rateKeys = rates.keys()
   c = []

   weirdCnt = 0
   weirdThresh = 5
   # for each host in turn...
   for i in up:
      spoofStr = getIp(i) + ':' + i

      if i not in rateKeys:
         sys.stderr.write( sys.argv[0] + ': Error: host ' + i + ' not in post\n' )
         continue

      dd = rates[i]
      #print dd

      # units of Data are "octets divided by 4", which means bytes/4, so 1 unit is 4 bytes.
      if hostMode == 'host':
         txData = dd[0]*4.0
         rxData = dd[1]*4.0
         txPkts = dd[2]
         rxPkts = dd[3]
      else:
         # remember to reverse rates 'cos we're looking at the switch end of the link
         txData = dd[1]*4.0
         rxData = dd[0]*4.0
         txPkts = dd[3]
         rxPkts = dd[2]

      if txData > dataMax or txData < 0:
         if weirdCnt < weirdThresh:
            print 'trapped weird txData', txData, 'host', i
         weirdCnt += 1
         txData = 0.0
      if rxData > dataMax or rxData < 0:
         if weirdCnt < weirdThresh:
            print 'trapped weird rxData', rxData, 'host', i
         weirdCnt += 1
         rxData = 0.0
      if txPkts > pktsMax or txPkts < 0:
         if weirdCnt < weirdThresh:
            print 'trapped weird txPkts', txPkts, 'host', i
         weirdCnt += 1
         txPkts = 0.0
      if rxPkts > pktsMax or rxPkts < 0:
         if weirdCnt < weirdThresh:
            print 'trapped weird rxPkts', rxPkts, 'host', i
         weirdCnt += 1
         rxPkts = 0.0

      if weirdCnt >= weirdThresh:
         print 'trapped many weird pkts/data - cnt', weirdCnt

      c.append( '/usr/bin/gmetric -S ' + spoofStr + ' -t float -n "ib_bytes_out" -u "bytes/sec"   -v %.2f\n' % txData )
      c.append( '/usr/bin/gmetric -S ' + spoofStr + ' -t float -n "ib_bytes_in"  -u "bytes/sec"   -v %.2f\n' % rxData )
      c.append( '/usr/bin/gmetric -S ' + spoofStr + ' -t float -n "ib_pkts_out"  -u "packets/sec" -v %.2f\n' % txPkts )
      c.append( '/usr/bin/gmetric -S ' + spoofStr + ' -t float -n "ib_pkts_in"   -u "packets/sec" -v %.2f\n' % rxPkts )

   return c


def parseArgs():
   global hostMode

   if len(sys.argv) != 2:
      print 'needs --host or --switch'
      sys.exit(1)
   if sys.argv[1] == '--host':
      hostMode = 'host'
   elif sys.argv[1] == '--switch':
      hostMode = 'switch'
   else:
      print 'needs --host or --switch'
      sys.exit(1)
      
if __name__ == '__main__':
   first = 1

   parseArgs()

   blah, blah, lidPortHost, blah = parseIbnetdiscover()
   #print lidPortHost, len(lidPortHost)

   sOld = {}
   while 1:
      if not first:
         time.sleep(sleepTime)

      up = listOfUpHosts(aliveTime)
      # hack ->
      #up = []
      #for i in range(1033,1152+1):
      #   up.append( 'v%d' % i )
      #if first:
      #   print 'up', up

      if not len(up):
         continue

      newNodesFound = compareIbToGanglia( lidPortHost, up )
      if newNodesFound:
         fail = runIbnetdiscover()
         if fail:
            sys.stderr.write( sys.argv[0] + ': Error: runIbnetdiscover failed. sleeping 30s\n' )
            time.sleep(30)
            continue
         blah, blah, lidPortHost, blah = parseIbnetdiscover()
         continue

      cmd, cnt = buildIbCmd( lidPortHost, up )
      #print 'cmd', cmd, 'cnt', cnt
      #print 'up', up

      # run ibperf
      r, err = runCommand( cmd )
      r = r.split('\n')
      #print 'r', r

      s = parseToStats( r, lidPortHost, up )
      #print 's', s

      rates = computeRates( sOld, s )
      #print 'rates', rates
      sOld = s

      if first:
         first = 0
         continue

      c = parseValsToGmetricLines(rates, up)
      if not len(c): # no hosts up?
         continue

      # pump vals into ganglia via gmetric
      p = subprocess.Popen( '/bin/sh', shell=False, bufsize=-1, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE )
      for i in c:
         p.stdin.write( i )
      out, err = p.communicate()

      # ignore gmetric's spoof info line, send the rest to stderr
      ep = 0
      for o in out.split('\n'):
         i = o.split()
         if len(i) and i[0].strip() != 'spoofName:':
            sys.stderr.write( sys.argv[0] + ': Error: gmetric stdout:' + str(i) + '\n' )
            ep = 1
      if ep:
         sys.stderr.flush()

      # print err if any
      if len(err):
         sys.stderr.write( sys.argv[0] + ': Error: gmetric stderr: ' +  str(err) + '\n' )
         sys.stderr.flush()

      #sys.exit(1)
