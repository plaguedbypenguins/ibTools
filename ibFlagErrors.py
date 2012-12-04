#!/usr/bin/env python

# (c) Robin Humble 2010
# licensed under the GPL v3

# look for the last 2 matched sets of ( ibclearerrors, ibcheckerrors, ibnetdiscover, perfstats ) and optionally the 'rebooted' file.
#  - typically would use the ( ibcheckerrors, and 2 perfstats ) before the last ( ibclearerrors )
#    - so look for ->
#          - a perfstats file + matching ibcheckerrors + rebooted + ibnetdiscover
#          - the perfstats before + matching ibclearerrors
#
#  - read ibnetdiscover to work out what is at both ends of all ports
#  - diff the 2 perfstats files to find traffic
#    - ignore the rebooted nodes (and switch ports) in that interval,
#      either by using the 'rebooted24' file or from current ganglia uptimes less than the first perfstats file
#  - compare traffic to errors
#  - print results
#
# should also read ibdiagnet to look for links the wrong speed, although this
# into is also returned by netdiscover so use that instead.

import os, sys, getopt, time, cPickle, math
from ibTracePorts import parseIbnetdiscover, findMostRecentFile, findMostRecentFiles
from hms import hms
try:
    from pbsMauiGanglia import gangliaStats
except:
    sys.stderr.write('warning: import of gangliaStats failed. detection of rebooted nodes in interval is disabled.\n')
    gangliaStats = None

MB = 1024.0*1024.0
GB = 1024.0*MB
TB = 1024.0*GB

ibDir = '/root/ib'
normalErrors = ( 'SymbolErrors', 'RcvErrors', 'LinkRecovers', 'LinkDowned', 'XmtDiscards' )
alwaysShowErrors = ( 'LinkRecovers', 'LinkDowned', 'XmtDiscards' )
noUptime = 'cmm'
minErrCount = 3
ibSymErrThresh = 1.0e-12
#ibRcvErrThresh = 1500*ibSymErrThresh   # for pkts
ibRcvErrThresh = ibSymErrThresh
errMax = { 'SymbolErrors':65535, 'RcvErrors':65535, 'LinkRecovers':255, 'LinkDowned':255 }
# qdr
portRate = '4xQDR'
lineRate = 40*GB*0.8   # QDR, 0.8 for data rate ~= Sun's 120 errs/hour
# fdr14
portRate = '4xFDR'
lineRate = 56*GB*(64.0/66.0)   # FDR

lowToo = 0
symErrsByTime = 1
debug = 0

# generic terms are
#   leaf == in-shelf switches closest to compute nodes
#   lc == line card for 2nd level of fat tree in big central switches
#   fc == fabric card for top level of fat tree   ""     ""

namingScheme='mellanox'  # lc = MF0;sx6536-5a:SXX536/L05/U1, fc = MF0;sx6536-2a:SXX536/S01/U1, leaf = ib001..
namingScheme='sun'       # leaf = qnem-rack-shelf{a,b} or M2-*, lc,fc = M9-{LC,FC}-#{a-d}


# from
#   http://www.openfabrics.org/downloads/dapl/documentation/uDAPL_ofed_testing_bkm.pdf
#   http://archiv.tu-chemnitz.de/pub/2007/0003/data/StefanWorm-MonitoringClusterComputers_Thesis2007.pdf
errVerbose = { 'SymbolErrors':'minor link errors detected on physical lanes. No action is required except if counter is increasing along with LinkRecovers',
               'LinkRecovers':'successfully completed link error recoveries. If this is increasing along with SymbolErrors this may indicate a bad link, run ibswportwatch.pl on this port',
               'LinkDowned':'failed link error recoveries (link down). Number of times the port has gone down (Usually for valid reasons)',
               'RcvErrors':'received packets containing an error. This is a bad link, if the link is internal to a switch try setting SDR, otherwise check the cable',
               'RcvRemotePhysErrors':'packets that are received with a bad packet end delimiter. This indicates a problem ELSEWHERE in the fabric.',
               'XmtDiscards':'outbound packets discarded because the port is down or congested. This is a symptom of congestion and may require tweeking either HOQ or switch lifetime values',
               'XmtConstraintErrors':'packets not transmitted from the switch\'s physical port. This is a result of bad partitioning, check partition configuration.',
               'RcvConstraintErrors':'packets received on the switch port that are discarded. This is a result of bad partitioning, check partition configuration.',
               'LinkIntegrityErrors':'times that the count of local physical errors exceeded the specified threshold. May indicate a bad link, run ibswportwatch.pl on this port',
               'ExcBufOverrunErrors':'consecutive (receive) buffer overrun errors. This is a flow control state machine error and can be caused by packets with physical errors',
               'VL15Dropped':'incoming management packets dropped due to resource limitations. check with ibswportwatch.pl, if increasing in SMALL incriments, OK',
               'RcvSwRelayErrors':'received packets that were discarded because they could not be forwarded by the switch. This counter can increase due to a valid network event' }

def findGroupsOfFiles(d):
    """find tuples of perfstats, ibcheckerrors, rebooted (optional), ibnetdiscover, ibclearerrors
       where the filenames must all have the same prefix except ibclearerrors
       which needs to have a timestamp within maxAfterTime of last file in the group (perfstats)
       
       also, any misc 'ibclearerrors' on its own invalidates the group(s) following it until
       the next ibclearerrors"""

    suffixes = ( 'perfstats', 'ibcheckerrors', 'ibnetdiscover', 'ibclearerrors', 'rebooted' )
    mandatory = 3   # list the mandatories first...
    maxAfterTime = 600

    files = os.listdir( d )
    byTime = []
    for f in files:
        if f.split('.')[-1] not in suffixes:
            continue
        m = os.path.getmtime( d + '/' + f)
        byTime.append(( m, f ))

    # loop through and purge all solo ibnetdiscovers as if they occur in
    # the middle of a group they can screw up the group detection

    # first group by prefix
    g = {}
    for b in byTime:
        t, f = b
        prefix, suffix = f.split('.')
        if prefix not in g.keys():
           g[prefix] = []
        g[prefix].append((suffix,b))

    # then loop through and del solo's
    f = {}
    for i,d in g.iteritems():
       if len(d) != 1:
          f[i] = d
          continue
       suffix,b = d[0]
       if suffix != "ibnetdiscover":
          f[i] = d
       #else:
       #   print 'deleting',d
    del g

    # then loop through and put byTime back together
    bt = []
    for i,d in f.iteritems():
       for j in d:
          suffix,b = j
          bt.append(b)
    del f
    byTime = bt

    # ok, so back to having a byTime, but now with no solo ibnetdicovers
    byTime.sort()
    #print byTime[-20:]

    # loop through and group
    groups = []
    g = {}
    prevPrefix = ''
    prevTime = -1
    for b in byTime:
        t, f = b
        prefix, suffix = f.split('.')
        #print 'prefix, suffix', prefix, suffix

        inGroup = 0
        if suffix == 'ibclearerrors' and prevTime + maxAfterTime > t:
            inGroup = 1
        prevTime = t

        if prevPrefix == prefix:
            inGroup = 1

        if not inGroup:   # next group
            groups.append(g)
            g = {}

        g[suffix] = b
        prevPrefix = prefix

    if len(g):
        groups.append(g)
    #print 'groups', groups[-2:]

    # check and tag incomplete groups
    for i in range(len(groups)):
        g = groups[i]
        k = g.keys()

        fail = 0
        for s in suffixes[:mandatory]:
            if s not in k:
                fail = 1

        clear = 0
        if 'ibclearerrors' in k:
            clear = 1

        if fail and not clear:  # harmless. ignore
            g['state'] = 'ignore'
        elif fail and clear:  # really just a clear on its own
            g['state'] = 'clear'
        elif not fail and clear:  # the normal case
            g['state'] = 'ok'
        elif not fail and not clear: # some partial state - probably a gather_quick without a clear
            g['state'] = 'semi-ok'
        else:
            print 'impossible'
            sys.exit(1)

        groups[i] = g

    if debug:
        for i in range(len(groups)-20,len(groups)):
            if i < 0:
                continue
            print 'group[%d]' % i, groups[i]

    # make a list of possible airs of groups that can be compared
    pairs = []
    end = len(groups)
    i = -1
    prevS = 'ignore'
    lastOk = -1
    prevI = -1
    while i < end-1:
        i += 1
        g = groups[i]
        s = g['state']

        # skip all ignore's
        while s == 'ignore' and i < end-1:
            i += 1
            g = groups[i]
            s = g['state']
        if i == end-1 and s == 'ignore':
            continue

        # can be 'ok' or 'clear' or 'semi-ok'
        #  - 2 'ok's in a row is best
        #  - any 'ok' combined with a (sequence of) 'semi-ok' is fine, as long as there's no 'clear's inbetween
        #  - any 'ok' back to the prev 'ok' is fine, again no 'clear's allowed
        if prevS == 'ok' and s == 'ok':
            pairs.append( (prevI,i) )
        elif s == 'semi-ok' and lastOk != -1:
            pairs.append( (lastOk,i) )
        elif s == 'ok' and lastOk != -1:
            pairs.append( (lastOk,i) )

        prevI = i
        prevS = s

        if s == 'ok':
            lastOk = i
        elif s == 'clear':
            lastOk = -1

    if debug:
         print 'last few pairs', pairs[-10:], 'end', end
         #sys.exit(1)

    return groups, pairs


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

def findUptimes(all):
    now = time.time()  # seconds since 1970
    uptime = {}
    for host in all.keys():
        if host[:len(noUptime)] == noUptime:
            continue
        try:
            uptime[host] = now - int(all[host]['boottime'])
        except:
            continue
    return uptime

def uptimes():
    if gangliaStats == None:
       return None, None
    g = gangliaStats()
    all = g.getAll()

    deadTimeout = 300
    up, down = findUpDown(all, deadTimeout)
    uptime = findUptimes(all)

    return uptime, down

# doesn't entirely work to remove non-printable chars. is easier just make ibcheckerrors print without colour instead.
def filter_non_printable(str):
   return ''.join([c for c in str if ord(c) > 31 or ord(c) == 9])

def parseIbcheckerrors(allErrs, ibCheckFile=None):
   f = ibCheckFile
   if f == None:
       suffix = 'ibcheckerrors'
       f, fTime = findMostRecentFile( ibDir, suffix )
   print 'using', f, 'for errors'
   lines = open( ibDir + '/' + f, 'r' ).readlines()
   #print lines

   # big problems with the fabric ->
   #
   # ibwarn: [22811] _do_madrpc: recv failed: Connection timed out
   # ibwarn: [22811] mad_rpc: _do_madrpc failed; dport (DR path slid 0; dlid 0; 0,1,31,18,3,31,17)
   # ibwarn: [22811] handle_port: NodeInfo on DR path slid 0; dlid 0; 0,1,31,18,3,31,17 failed, skipping port
   # ibwarn: [22811] _do_madrpc: recv failed: Invalid argument
   # ibwarn: [22811] mad_rpc: _do_madrpc failed; dport (DR path slid 0; dlid 0; 0,1,31,18,3,31)
   # ibwarn: [22811] discover: can't reach node DR path slid 0; dlid 0; 0,1,31,18,3,31 port 18
   # 
   # normal stuff ->
   #
   # #warn: counter SymbolErrors = 65534 (threshold 10) lid 2 port 255
   # #warn: counter LinkDowned = 254 (threshold 10) lid 2 port 255
   # Error check on lid 2 (0x0021283a89190040 qnem-13-3a) port all:  FAILED 
   # #warn: counter SymbolErrors = 722 (threshold 10) lid 259 port 255
   # Error check on lid 259 (0x0021283a87820050 qnem-13-1b) port all:  FAILED 
   # #warn: counter SymbolErrors = 722 (threshold 10) lid 259 port 21
   # Error check on lid 259 (0x0021283a87820050 qnem-13-1b) port 21:  FAILED 
   # #warn: counter SymbolErrors = 11251     (threshold 10) lid 1447 port 12
   # #warn: counter LinkRecovers = 255       (threshold 10) lid 1447 port 12
   # #warn: counter RcvErrors = 2342         (threshold 10) lid 1447 port 12
   # Error check on lid 1447 (0x0021283a87760040 qnem-05-4a) port 12: FAILED
   # #warn: counter SymbolErrors = 13        (threshold 10) lid 989 port 1
   # #warn: counter RcvErrors = 13   (threshold 10) lid 989 port 1
   # Error check on lid 989 (v679 HCA-1) port 1: FAILED
   # #warn: counter SymbolErrors = 19        (threshold 10) lid 974 port 1
   # #warn: counter RcvErrors = 18   (threshold 10) lid 974 port 1
   # Error check on lid 974 (v668 HCA-1) port 1: FAILED

   # (later) also can get many lines like this which I'll ignore ->
   # ibwarn: [6329] dump_perfcounters: PortXmitWait not indicated so ignore this counter
   # ibwarn: [6381] dump_perfcounters: PortXmitWait not indicated so ignore this counter
   # ibwarn: [6438] dump_perfcounters: PortXmitWait not indicated so ignore this counter

   errs = {}
   for ll in lines:
      ll = ll.strip()
      l = ll.split()

      # ignore final summary lines
      if len(l) == 0 or l[0][:2] == "##":
         continue

      # important errors - TODO - return these...
      if l[0] == 'ibwarn:':
         if 'dump_perfcounters:' in l and 'PortXmitWait' in l:
            continue
         print ll
         continue

      #print l
      if l[0] == "#warn:":
         if l[-2] != "port":
            print 'expected a port in a #warn, not "', ll, '"'
            continue
         lid = int(l[-3])
         port = int(l[-1])
         err = l[2]
         errCnt = int(l[4])
         if port == 255:
            continue
         if not allErrs and err not in normalErrors:
            continue
         #print 'lid', lid, 'port', port, 'err', err, 'errCnt', errCnt
         key = ( lid, port )
         if key not in errs.keys():
            errs[key] = {}
            errs[key]['errs'] = []
         errs[key]['errs'].append( ( err, errCnt ) )
      elif l[0] == "Error":
         # ignore 255,all
         lid = int(l[4])
         port = l[-2].split(':')[0]
         if port == 'all':
            port = 255
         port = int(port)
         if port == 255:
            continue
         key = ( lid, port )
         name = ll.split('(')[1].split(')')[0]
         #print 'lid', lid, 'port', port, 'name', name
         if key not in errs.keys(): # all errs for this (lid,port) were skipped
            continue
         errs[key]['name'] = name

   return errs


def lidType( n ):
    """take switch chip name and figure out type"""
    if not len(n):
        return None
    if namingScheme == 'sun':
        # switch
        nn = n.split('-')
        if nn[0] == 'qnem':
            return 'qnem'
        if nn[0] == 'M2':
            return 'M2'
        # M9-2-LC-1b, M9-3-FC-6b
        if nn[0] != 'M9' or len(nn) < 3:
            #print 'parsing error', nn
            return None
        if nn[2] == 'LC':
            return 'LC'
        elif nn[2] == 'FC':
            return 'FC'
        #print 'unknown type', n
        return None
    elif namingScheme == 'mellanox':
        # switch
        nn = n.split('-')
        if nn[0] == 'qnem':
            return 'qnem'
        if nn[0] == 'M2':
            return 'M2'
        # M9-2-LC-1b, M9-3-FC-6b
        if nn[0] != 'M9' or len(nn) < 3:
            #print 'parsing error', nn
            return None
        if nn[2] == 'LC':
            return 'LC'
        elif nn[2] == 'FC':
            return 'FC'
        #print 'unknown type', n
        return None
    else:
        print 'unknown fabric naming scheme'
        return None

def filterHosts( uptime, fTime ):
    """find hosts that have been booted after a given time"""
    now = time.time()
    fileOld = now - fTime
    recent = []
    for u in uptime.keys():
        #print u, 'rebooted', uptime[u], 's ago, file', fileOld, 's, diff', fileOld - uptime[u], 's'
        if uptime[u] < fileOld:
            #print u, 'recently rebooted - dt', fileOld - uptime[u]
            recent.append( u )
    return recent

def findHostLid( hosts ):
    hl = []
    lids = []
    for h in hosts:
        found = 0
        for swLid, swPort, lid, hh in lph:
            if h == hh:
                found = 1
                #print h, 'swLid, swPort, lid', swLid, swPort, lid
                hl.append( (h, lid) )
                lids.append(lid)
        if not found:
            if debug:
                print 'warning - lid for host', h, 'not found'
            hl.append( (h, None) )
    return hl, lids

def restoreStats( fn ):
    f = open( fn, 'r' )
    s = cPickle.load( f )
    f.close()
    return s

def getTraffic( f0=None, f1=None ):
    fn0 = f0
    fn1 = f1
    if fn0 == None or fn1 == None:
        fn1, t, fn0, t = findMostRecentFiles( ibDir, 'perfstats' )
    print 'traffic', fn0, 'to', fn1
    s1 = restoreStats( ibDir + '/' + fn1 )
    s0 = restoreStats( ibDir + '/' + fn0 )
    return subStats( s0, s1 )

def subStats(s0, s1):
    # s[( lid, port, name )] = ( t, int[4] )  # PortXmitData PortRcvData PortXmitPkts PortRcvPkts
    s0k = s0.keys()
    s1k = s1.keys()

    d = {}
    for s in s0k:
        if s not in s1k:
            continue
        lid, port, name = s
        t0, d0 = s0[s]
        t1, d1 = s1[s]
        # units of Data are "octets divided by 4", which means bytes/4, so 1 unit is 4 bytes.
        d[(lid, port)] = ( s, 4*(d1[0] - d0[0]), 4*(d1[1] - d0[1]), d1[2] - d0[2], d1[3] - d0[3] )

    return d

def getP( s, lid, port ):
    try:
        d = s[(lid,port)]
    except:
        return None,None
    k, tx, rx, txPkts, rxPkts = d
    return txPkts, rxPkts

def getB( s, lid, port ):
    try:
        d = s[(lid,port)]
    except:
        return None,None
    k, tx, rx, txPkts, rxPkts = d
    return tx, rx

def printMB( s, lid, port, oLid, oPort ):
    tx, rx = getB( s, lid, port )
    otx, orx = getB( s, oLid, oPort )
    if tx != None:
        print 'tx/rx %.1f %.1f MB' % ( tx/MB, rx/MB ),
        traffic = ( tx, rx )
    else: # use other end of link
        if otx != None:  # note, reversed...
            print 'tx/rx %.1f %.1f MB (other end of link)' % ( orx/MB, otx/MB ),
            traffic = ( orx, otx )
        else:
            print 'no traffic info for either end of link available',
            traffic = ( None, None )
    return traffic

def addSymErrRateToErrs( errs, k, errorList, t ):
    tx, rx = errs[k]['b']
    orx, otx = errs[k]['b-otherEnd']

    if rx != None:
        src='this'
    elif orx != None:
        src='otherEnd'
        rx = orx
    else:
        src=None
    if src == None:
        return

    for errName, errCnt in errorList:
        if errName == 'SymbolErrors':
            if errCnt >= minErrCount:
                plus = 0
                if errCnt >= errMax[errName]:
                    plus = 1
                if symErrsByTime:
                    errs[k]['symErrRate'] = ( float(errCnt)/float(t*lineRate), src, plus )
                else:
                    errs[k]['symErrRate'] = ( float(errCnt)/float(rx*10), src, plus )
            else:
                errs[k]['symErrRate'] = ( None, 'skip', 0 )

def addRcvErrRateToErrs( errs, k, errorList ):
    #tx, rx = errs[k]['p']
    #orx, otx = errs[k]['p-otherEnd']
    tx, rx = errs[k]['b']
    orx, otx = errs[k]['b-otherEnd']

    if rx != None:
        src='this'
    elif orx != None:
        src='otherEnd'
        rx = orx
    else:
        src=None
    if src == None:
        return

    for errName, errCnt in errorList:
        if errName == 'RcvErrors':
            if errCnt >= minErrCount:
                plus = 0
                if errCnt >= errMax[errName]:
                    plus = 1
                errs[k]['rcvErrRate'] = ( float(errCnt)/float(rx*10), src, plus )
            else:
                errs[k]['rcvErrRate'] = ( None, 'skip', 0 )

def addAlwaysShowErrToErrs( errs, k, errorList ):
    for errName, errCnt in errorList:
        if errName in alwaysShowErrors:
            if errName in errMax.keys() and errCnt >= errMax[errName]:
                errs[k]['alwaysShowErr'] = 1
            else:
                errs[k]['alwaysShowErr'] = 0

def addRateErrs( switchTree, lph, rates, errs ):
    # check we have rates for all switch ports
    rk = rates.keys()
    for l in switchTree.keys():
        swName, swLid, a = switchTree[l]
        for p in a.keys():
            k = (swLid, p)
            if k not in rk:
                if k not in errs.keys():
                    errs[k] = {}
                    errs[k]['name'] = 'some switch chip'
                errs[k]['portRate'] = 'unknown'

    # check we have rates for all host ports
    for swLid, swPort, lid, hh in lph:
        k = (lid, 1)
        if k not in rk:
            if k not in errs.keys():
                errs[k] = {}
                errs[k]['name'] = hh + ' HCA-1'
            errs[k]['portRate'] = 'unknown'

    # loop over all rates and check they're ok
    for k in rk:
        if rates[k] != portRate:  # have an error
            if k not in errs.keys():
                errs[k] = {}
                lid, port = k
                t, name = findHostByLid( lph, switchTree, lid, port )
                if t == 'host':
                    errs[k]['name'] = name + ' HCA-1'
                elif t == 'switch':
                    errs[k]['name'] = 'wrongGuid ' + name
                else:
                    errs[k]['name'] = 'no idea'
            errs[k]['portRate'] = rates[k]


def findHostByLid( lph, switchTree, lid, port ):
    for swLid, swPort, l, hh in lph:
        if l == lid:
            return ( 'host', hh )
    if (lid, port) in switchTree.keys():
        swName, swLid, a = switchTree[(lid,port)]
        return ('switch', swName)
    return (None, None)

def printErrLine( e, tt ):
    # ('qnem-07-2b', 1536, 25) to ('v622', 911, 1) qnem<->host tx/rx 112352.4 105136.9 MB errs [('SymbolErrors', 65535), ('RcvErrors', 28)] errs 65535 high BER 7.4e-08

    if 'nlp' not in e.keys():
        # a lid in the ignore list doesn't have any extra keys
        # shouldn't happen now...
        print 'nlp error', e
        sys.exit(1)
        return ''

    if 'type' not in e.keys():   # shouldn't happen
        print 'type error', e
        sys.exit(1)
        return ''

    t = e['type']
    if t not in tt:
        return t

    if 'ignore' in e.keys() and not allHosts and 'portRate' not in e.keys():
        return t

    if not lowToo:
        skipCnt = 0
        if 'portRate' in e.keys():
            skipCnt += 1
        if 'alwaysShowErr' in e.keys():
            skipCnt += 1
        if 'symErrRate' in e.keys():
            skipCnt += 1
        if 'rcvErrRate' in e.keys():
            skipCnt += 1

        skip = 0
        if 'symErrRate' in e.keys():
            rate, src, plus = e['symErrRate']    
            if src == 'skip':
                skip += 1
            elif rate < ibSymErrThresh:
                skip += 1
        if 'rcvErrRate' in e.keys():
            rate, src, plus = e['rcvErrRate']    
            if src == 'skip':
                skip += 1
            elif rate < ibRcvErrThresh:
                skip += 1
        if skipCnt == 0 or skip == skipCnt:
            return t

    for et, er, en in ( ('symErrRate', ibSymErrThresh, 'BER'), ('rcvErrRate', ibRcvErrThresh, 'Rcv') ):
        if et in e.keys():
            rate, src, plus = e[et]    
            p = ' '
            if plus:
                p = '>'
            if src == 'skip':
                print '    -    ',
            elif rate > er:
                print p + '*%#7.2g' % rate,
            else:
                print p + ' %#7.2g' % rate,
            if src == 'otherEnd':
                print '[' + en + ' from otherEnd traffic]',
        else:
            print ' '*9,

    if 'ignore' in e.keys() and e['ignore'] in ('lid', 'host'):  # this is a host that's been rebooted/down
        print '( *** %5s, %4d, %2d)' % e['nlp'], 'to',
    else:
        print '(%10s, %4d, %2d)' % e['nlp'], 'to',

    if 'nlp-otherEnd' in e.keys():
        if 'ignore' in e.keys() and e['ignore'] in ('port'): # otherEnd is rebooted/down host
            print '( *** %5s, %4d, %2d)' % e['nlp-otherEnd'],
        else:
            print '(%10s, %4d, %2d)' % e['nlp-otherEnd'],
    else:
        print '<unknown in topology>',

    #print e['type'],

    tx, rx = errs[k]['b']
    otx, orx = errs[k]['b-otherEnd']
    # @@@ do some sort of warning about -ve traffic...
    src = ''
    if rx == None:
         tx = orx
         rx = otx
         src = '(from other end)'
    if rx == None:
         print 'no traffic info available',
    else:
         #print 'tx/rx %.1f %.1f GB' % ( tx/GB, rx/GB ),
         if rx < 0:
             print 'negative %4.1f MB' % ( rx/MB ),
         elif rx > 0.1*TB:
             print 'rx %4.1f TB' % ( rx/TB ),
         elif rx > 0.1*GB:
             print 'rx %4.1f GB' % ( rx/GB ),
         else:
             print 'rx %4.1f MB' % ( rx/MB ),
         if src != '':
             print src,
         if tx != None and orx != None and rx != None and otx != None:
             if tx > 0 and orx > 0 and rx > 0 and otx > 0:
                 absErr = math.sqrt((tx - orx)**2 + (rx - otx)**2)
                 relErr = absErr/(0.5*math.sqrt((tx + orx)**2 + (rx + otx)**2))
                 if absErr/MB > 100 and relErr > 0.1:
                     print '(warning: orx/otx %.1f %.1f GB)' % ( orx/GB, otx/GB ),

    tx, rx = errs[k]['p']
    otx, orx = errs[k]['p-otherEnd']
    # @@@ do some sort of warning about -ve pkts...
    src = ''
    if rx == None:
         tx = orx
         rx = otx
         src = '(from other end)'
    if rx == None:
         print 'no traffic info available',
    else:
         #print 'tx/rx %.1f %.1f MPkts' % ( tx/MB, rx/MB ),
         if rx < 0:
             print 'negative %4.1f MPkts' % ( rx/MB ),
         elif rx > 0.1*TB:
             print '%4.1f TPkts' % ( rx/TB ),
         elif rx > 0.1*GB:
             print '%4.1f GPkts' % ( rx/GB ),
         else:
             print '%4.1f MPkts' % ( rx/MB ),
         if src != '':
             print src,
         if tx != None and orx != None and rx != None and otx != None:
             if tx > 0 and orx > 0 and rx > 0 and otx > 0:
                 absErr = math.sqrt((tx - orx)**2 + (rx - otx)**2)
                 relErr = absErr/(0.5*math.sqrt((tx + orx)**2 + (rx + otx)**2))
                 if absErr/MB > 1 and relErr > 0.1:
                     print '(warning: orx/otx %.1f %.1f MPkts)' % ( orx/MB, otx/MB ),

    if 'errs' in e.keys():   # might not be if we're just reporting bad rates
        print 'errs', e['errs'],

    if 'otherEndErrors' in e.keys():
        print 'ALSO errs at other end',

    if 'portRate' in e.keys():
        if e['portRate'] == 'unknown':
            print '** ?x?DR **',
        else:
            print '**', e['portRate'], '**',

    #for errName, errCnt in e['errs']:
    #    if errName == 'SymbolErrors':
    #        print 'errs', errCnt,

    print

    return t

def printErrDesc():
   l = 0
   for e, d in errVerbose.iteritems():
      if len(e) > l:
          l = len(e)
   for e, d in errVerbose.iteritems():
      print ' '*(l - len(e)), e, '-',  d

def usage():
   print 'usage:', sys.argv[0], '[-h|--help] [-r|--rebooted] [-a|--allerrs] [-m M|--minerrs=M] [-L|--list] [-l|--low] [-E|--errdesc] [-t|--timedata] [N]'
   print '  --rebooted   do not ignore rebooted hosts/lids'
   print '  --allerrs    report all types of errors, not just', normalErrors
   print '  --minerrs    do not generate BER/Rcv for SymbolErrors/RcvErrors count less than M. default', minErrCount
   print '  --list       list possible sets of IB files that could be used, and then exit'
   print '  --low        also output lids with low error count'
   print '  --errdesc    print some descritions of IB errors'
   print '  --timedata   toggle SymbolErrors by time or by rx data, default by',
   if symErrsByTime:
      print 'time'
   else:
      print 'rx data'
   print 'N is an optional integer that counts back in time through allowable sets of'
   print 'IB files. if omitted the most recent set of files is used (same as N=1).'
   print 'eg. N=2 specifies the second last set of files.'
   sys.exit(0)

if __name__ == '__main__':
    try:
        opts, args = getopt.getopt( sys.argv[1:], 'hdram:LlEt', ['help', 'debug', 'rebooted', 'allerrs', 'minerrs=', 'list', 'low', 'errdesc', 'timedata' ] )
    except getopt.GetoptError:
         usage()  # print help information and exit

    allHosts = 0
    allErrs = 0
    listOnly = 0
    for o, a in opts:
         if o in ('-h', '--help'):
             usage()
         elif o in ('-d', '--debug'):
             debug = 1
         elif o in ('-r', '--rebooted'):
             allHosts = 1
         elif o in ('-a', '--allerrs'):
             allErrs = 1
         elif o in ('-m', '--minerrs=*'):
             try:
                 minErrCount = int(a)
             except:
                 usage()
         elif o in ('-L', '--list'):
             listOnly = 1
         elif o in ('-l', '--low'):
             lowToo = 1
         elif o in ('-E', '--errdesc'):
             printErrDesc()
             sys.exit(0)
         elif o in ('-t', '--timedata'):
             symErrsByTime += 1
             symErrsByTime %= 2

    if len(args) > 1:
        usage()
    elif len(args) == 0:
        # pick the last pair by default
        pick = 1
    else:
        try:
            pick = int(args[0])
        except:
            usage()

    groups, pairs = findGroupsOfFiles( ibDir )
    if len(pairs) == 0:
        print 'no valid sets of ib error and stats files found. need to gather some stats first?'
        sys.exit(1)

    if listOnly:
        cnt = 0
        for i in range(len(pairs),0,-1):
            p0, p1 = pairs[i-1]
            grp0 = groups[p0]
            grp1 = groups[p1]
            interval = grp1['perfstats'][0] - grp0['ibclearerrors'][0]
            print cnt, 'interval', hms(interval), grp0['perfstats'][1].split('.')[0], 'to', grp1['perfstats'][1].split('.')[0]
            cnt += 1
        sys.exit(0)

    if allHosts:
        print 'warning: rebooted/down hosts are included'
    else:
        print 'only displaying non-rebooted/down hosts'

    if allErrs:
        print 'showing all errors, not just', normalErrors
    else:
        print 'warning: only displaying', normalErrors

    if lowToo:
        print 'showing ports with low error counts'
    else:
        print 'only displaying ports with error counts >', minErrCount

    if symErrsByTime:
        print 'symbol err rate calculated by time and line rate, threshold', ibSymErrThresh
    else:
        print 'symbol err rate calculated by rx data, threshold', ibSymErrThresh
    print 'rcv err rate calculated by rx data, threshold', ibRcvErrThresh

    if pick >= len(pairs):
        print 'sorry. there aren\'t that many sets of IB files. max', len(pairs), 'pick', pick
        sys.exit(1)
    elif pick < 0:
        print 'IB file set number must be >0'
        sys.exit(1)

    pair = p0, p1 = pairs[-pick]
    grp0 = groups[p0]
    grp1 = groups[p1]
    if debug:
        print 'pick', pick, 'using', pair, 'grp0', grp0, 'grp1', grp1

    ibNetFile = grp1['ibnetdiscover'][1]
    switchTree, byName, lph, rates = parseIbnetdiscover( ibNetFile=ibNetFile )
    #print 'switchTree (len', len(switchTree), ')' # , switchTree  # by switch LID
    #print 'byName (len', len(byName), ')' # , byName  # by hostname
    #print 'lph (len', len(lph), ')' # , lph  # swlid, swport, lid, host
    #print 'rates (len', len(rates), ')', rates  # swlid, swport, lid, host

    errs = parseIbcheckerrors( allErrs, grp1['ibcheckerrors'][1] )
    #print 'errs', errs  # by (lid, port), but hostname in 'name' field for hosts

    if 'rebooted' in grp1.keys():
        # use rebooted file...
        f = open( ibDir + '/' + grp1['rebooted'][1], 'r' )
        ff = f.readlines()
        f.close()
        ignore = []
        for i in ff:
            if len(i) > 0 and i[0] == '#':
                continue
            ignore.append( i.strip() )
        print 'rebooted in interval', ignore,
    else:
        uptime, down = uptimes()
        #print 'len(uptime)', len(uptime), 'uptime', uptime   # uptimes by hostname
        #print 'len(down)', len(down), 'down', down

        if uptime == None:
            ignore = []
        else:
            # NOTE: this is the time of the grp0 files - need machines rebooted since grp0
            fTime = grp0['ibclearerrors'][0]
            ignore = filterHosts( uptime, fTime )

            # no idea about currently down nodes, so assume they are evil
            ignore.extend(down)

            print 'warning: no rebooted file. using ganglia rebooted/down in last', hms(time.time() - fTime), ignore,

    blah, ignoreLid = findHostLid( ignore )
    print 'lids', ignoreLid

    interval = grp1['perfstats'][0] - grp0['ibclearerrors'][0]
    print 'errors in interval of', hms(interval), '(h:m:s)'

    s = getTraffic( f0=grp0['perfstats'][1], f1=grp1['perfstats'][1] )

    print 'tuples are (name, lid, port)'
    print '* denotes above threshold, - denotes not enough errors, ** denotes non-' + portRate + ' link, *** denotes rebooted'

    # find SDR/DDR or 1x 2x links
    addRateErrs( switchTree, lph, rates, errs )

    # @@@ look for all -ve traffic which might indicate chip reset

    # @@@ IB sym error rates should be relative to host uptime if the host has been rebooted in the interval

    # process errs and add lots of info to the errs dict
    for k in errs.keys():
        lid, port = k

        if lid in ignoreLid:
            errs[k]['ignore'] = 'lid'

        # host
        n = errs[k]['name'].split()
        if ( len(n) == 2 and n[1] == 'HCA-1' ) or ( len(n) == 1 and n[0] == 'HCA-1' ) or ( lid not in switchTree.keys() ):
            host = n[0]
            if host in ignore and 'ignore' not in errs[k].keys():
                errs[k]['ignore'] = 'host'
            errs[k]['nlp'] = (host, lid, port)
            swPort, swName, swLid = byName[host]
            errs[k]['nlp-otherEnd'] = (swName, swLid, swPort)

            assert( lidType(swName) == 'qnem' or lidType(swName) == 'M2' )
            errs[k]['type'] = 'host<->' + lidType( swName )

            if (swLid, swPort) in errs.keys():
                errs[k]['otherEndErrors'] = 1

            errs[k]['b'] = getB( s, lid, port )
            errs[k]['p'] = getP( s, lid, port )
            errs[k]['b-otherEnd'] = getB( s, swLid, swPort )
            errs[k]['p-otherEnd'] = getP( s, swLid, swPort )

            if 'errs' in errs[k].keys():
                errorList = errs[k]['errs']
                addSymErrRateToErrs( errs, k, errorList, interval )
                addRcvErrRateToErrs( errs, k, errorList )
                addAlwaysShowErrToErrs( errs, k, errorList )

            continue


        # switch
        #print 'lid', lid
        swName, swLid, a = switchTree[lid]
        assert( swLid == lid )
        if port in a.keys():
            oSwName, oSwLid, oPort = a[port]

        errs[k]['nlp'] = (swName, lid, port)

        # find the other end
        if port not in a.keys():
            continue

        oSwName, oSwLid, oPort = a[port]
        errs[k]['nlp-otherEnd'] = (oSwName, oSwLid, oPort)

        ## hack
        #if swName == '-':
        #   swName = 'M2-5'

        # link type could be
        #   M2 <-> node
        #   qnem <-> node
        #   qnem <-> qnem
        # or either way around of these ->
        #   qnem <-> LC
        #   LC <-> FC
        #   LC <-> M2

        if oSwLid not in switchTree.keys():
            assert( lidType(swName) == 'qnem' or lidType(swName) == 'M2' )
            tt = lidType(swName) + '<->host'
            if oSwLid in ignoreLid or oSwName in ignore:
                errs[k]['ignore'] = 'port'
        else:
            t = lidType(swName)
            ot = lidType(oSwName)
            if (t, ot) == ('qnem', 'qnem'):
                tt = 'qnem<->qnem'
            elif t == 'qnem':
                assert( ot == 'LC' )
                tt = 'qnem<->LC'
            elif t == 'FC':
                assert( ot == 'LC' )
                tt = 'FC<->LC'
            elif t == 'M2':
                assert( ot == 'LC' )
                tt = 'M2<->LC'
            elif t == 'LC':
                if ot == 'M2':
                    tt = 'LC<->M2'
                elif ot == 'FC':
                    tt = 'LC<->FC'
                elif ot == 'qnem':
                    tt = 'LC<->qnem'
                else:
                    print 'LC connected to unknown - illegal link?',
                    tt = None
                    exit(1)
        errs[k]['type'] = tt

        if (oSwLid, oPort) in errs.keys():
            errs[k]['otherEndErrors'] = 1

        errs[k]['b'] = getB( s, lid, port )
        errs[k]['p'] = getP( s, lid, port )
        errs[k]['b-otherEnd'] = getB( s, oSwLid, oPort )
        errs[k]['p-otherEnd'] = getP( s, oSwLid, oPort )

        if 'errs' in errs[k].keys():
            errorList = errs[k]['errs']
            addSymErrRateToErrs( errs, k, errorList, interval )
            addRcvErrRateToErrs( errs, k, errorList )
            addAlwaysShowErrToErrs( errs, k, errorList )

    if debug:
        print errs

    # sort by BitErrorRate
    ber = []
    noBer = []
    for k in errs.keys():
        #print k
        if 'symErrRate' in errs[k].keys():
            rate, src, plus = errs[k]['symErrRate']
            if src == 'skip':
                noBer.append(k)
            else:
                ber.append((rate, k))
        else:
            noBer.append(k)

    ber.sort()
    ber.reverse()

    #for r, k in ber:
    #    if r > ibSymErrThresh:
    #        print r, errs[k]
    #    else:
    #        print 'low ber', errs[k]
    #for k in noBer:
    #    print 'unknown ber', errs[k]

    missed = []
    covered = []
    for tt in ( ('host<->qnem'), ('qnem<->host'), ('M2<->host', 'host<->M2'), ('qnem<->qnem'), ('qnem<->LC', 'LC<->qnem', 'LC<->M2', 'M2<->LC'), ('LC<->FC', 'FC<->LC') ):
        print
        print tt
        print '    Sym       Rcv'
        for r, k in ber:
            t = printErrLine(errs[k], tt)
            if t in tt:
                if t not in covered:
                    covered.append(t)
            elif t not in missed:
                missed.append(t)
        for k in noBer:
            t = printErrLine(errs[k], tt)
            if t in tt:
                if t not in covered:
                    covered.append(t)
            elif t not in missed:
                missed.append(t)

    #print 'missed', missed
    #print 'covered', covered
    for m in missed:
        if m not in covered:
            print 'error - missed type', m
