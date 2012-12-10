#!/usr/bin/env python

# (c) Robin Humble 2010
# licensed under the GPL v3

# slurp up ibnetdiscover and check the topology of all links

import os, sys, string
from ibTracePorts import parseIbnetdiscover
from ibFlagErrors import lidType

ibDir = '/home/rjh'
ibDir = '/root/ib'

# qnem to use as a template for connectivity of all
canonicalQnem=1
canonicalQnem=2

def findLidsByType( switchTree, t ):
    l = []
    for k in switchTree.keys():
        swName, swLid, a = switchTree[k]
        assert( k == swLid )
        if lidType( swName ) == t:
            l.append( k )
    return l

def findLidByName( switchTree, lph, n ):
    # look through switches
    if switchTree != None:
        for k in switchTree.keys():
            swName, swLid, a = switchTree[k]
            if swName == n:
                return swLid
    # look thought hosts
    if lph != None:
        for swLid, swPort, lid, host in lph:
            if host == n:
                return lid
    return None

def qnemIndexToName( i ):
    r = (i-1)/4
    j = i - r*4
    s = 'qnem-%.2d-%d' % ( r+1, j )
    return s

def m9number( name ):
    # convert eg. M9-4-LC-1c to '4'
    return int(name.split('-')[1])

def lcChipName( name ):
    # convert eg. M9-4-LC-1c to '1c'
    return name.split('-')[3]

def isM2( name ):
    return ( name[:2] == 'M2' )

if __name__ == '__main__':
   if len(sys.argv) == 2:
       switchTree, byName, lph, r = parseIbnetdiscover( ibNetFile=sys.argv[1] )
   else:
       switchTree, byName, lph, r = parseIbnetdiscover( ibDir=ibDir )
   print 'switchTree (len', len(switchTree), ')' # , switchTree  # by switch LID
   print 'byName (len', len(byName), ')' # , byName  # by hostname
   print 'lph (len', len(lph), ')' # , lph  # swlid, swport, lid, host

   chipPorts = 36
   lcFcLinks = chipPorts/2    # full fat tree
   lcQnemLinks = chipPorts/2  # full fat tree
   lcM2Links = 12
   m9s = ( 1,2,3,4 )
   qnemLcLinks = 12   # 12 to nodes, 12 to LC, 12 to other qnem

   ioLC = ( 'M9-1-LC-5d', 'M9-2-LC-5d', 'M9-3-LC-5d', 'M9-4-LC-5d' )
   partialLC = ( 'M9-1-LC-4c', 'M9-2-LC-4c', 'M9-3-LC-4c', 'M9-4-LC-4c', 'M9-1-LC-5c', 'M9-2-LC-5c', 'M9-3-LC-5c', 'M9-4-LC-5c', 'M9-1-LC-5d', 'M9-2-LC-5d', 'M9-3-LC-5d', 'M9-4-LC-5d' )

   # find all LC chips
   lc = findLidsByType( switchTree, 'LC' )
   for k in lc:
       swName, swLid, a = switchTree[k]
       fc = []
       m2 = []
       q = []
       keys = a.keys()
       #if len(keys) != chipPorts:
       #    print 'error:', len(keys), 'links from LC', swName, 'should be', chipPorts

       for p in a.keys():
           name, lid, port = a[p]
           t = lidType( name )
           if t == 'FC':
               fc.append(p)
           elif t == 'leaf':
               if isM2(name):
                   m2.append(p)
               else:
                   q.append(p)
           else:
               print 'unknown/illegal link on', swName, 'port', p, 'is type', t, '(details', a[p], ')'

       if len(fc) != lcFcLinks:
           print 'error:', len(fc), 'links from LC', swName, 'to FCs should be', lcFcLinks
       if swName in partialLC:
           if len(q) != 0:
               print 'error:', len(q), 'links from LC', swName, 'to qnems should be', 0
       else:
           if len(q) != lcQnemLinks:
               print 'error:', len(q), 'links from LC', swName, 'to qnems should be', lcQnemLinks
       if swName in ioLC and len(m2) != lcM2Links:
           print 'error:', len(m2), 'links from LC', swName, 'to m2 should be', lcM2Links

       ports = a.keys()
       ports.sort()
       #print swName, 'ports', ports
       #print 'q', q

       if not len(q):
           continue

       # check they are connected to the correct qnems in the correct order
       # each LC should go to 6 qnem chips, in 3 shelves.
       names = {}
       for p in q:
           name, lid, port = a[p]
           if name not in names:
               names[name] = 1
           else:
               names[name] += 1
       if len(names.keys()) != 6:
           print 'error: LC', swName, 'doesn\'t go to 6 qnem chips'
       for n, cnt in names.iteritems():
           if cnt != 3:
               print 'error: LC', swName, 'hasn\'t got 3 links to', n, ', has', cnt
       #print names

       # LC-{0,1,2,3} are ->
       # eg. M9-[1-4]-LC-0a is connected to qnem-01-{1,2,3}{a,b}                  -> qnems #1-3
       #     M9-[1-4]-LC-0b     ""          qnem-01-{4}{a,b} qnem-02-{1,2}{a,b}   -> qnems #4-6
       #     M9-[1-4]-LC-1a     ""          qnem-02-{3,4}{a,b} qnem-03-{1}{a,b}   -> qnems #7-9
       #     M9-[1-4]-LC-1b     ""          qnem-03-{2,3,4}{a,b}                  -> qnems #10-12
       # and on the other 'side' things are kinda mirrored ->
       #     M9-[1-4]-LC-0d  qnem-07-{1,2,3}            -> qnems #25-27
       #     M9-[1-4]-LC-0c  qnem-07-{4} qnem-08-{1,2}  -> qnems #28-30
       #     M9-[1-4]-LC-1d  qnem-08-{3,4} qnem-09-{1}  -> qnems #31-33
       #     M9-[1-4]-LC-1c  qnem-09-{2,3,4}            -> qnems #34-36
       #
       # LC-{4,5} are ->
       #    LC-4{a,b,d} and LC-5{a,b} are wired up differently... sigh
       # LC-4a are qnem-13-{1,2,3}                -> qnem #49-51
       # LC-4b are qnem-13-{4} and qnem-14-{1,2}  ->       52-54
       # LC-5a are qnem-14-{3,4} and qnem-15-{1}  ->       55-57
       # LC-5b are qnem-15-{2,3,4}                ->       58-60
       # LC-4d are qnem-16-{1,2,3}                ->       61-63

       l = int(swName[-2])
       if l in ( 0, 1, 2, 3 ):
           if swName[-1] in ( 'a', 'b' ):
               qnemStart = 6*l + 1
               if swName[-1] == 'b':
                   qnemStart += 3
           elif swName[-1] in ( 'c', 'd' ):
               qnemStart = 6*l + 25
               if swName[-1] == 'c':
                   qnemStart += 3
           else:
               print 'error: unknown LC 0-3 qnem suffix', swName[-1]
       else:  # LC 4,5
           if swName[-1] in ( 'a', 'b' ):
               qnemStart = 6*l + 25
               if swName[-1] == 'b':
                   qnemStart += 3
           elif swName[-1] in ( 'd' ):   # 4d
               qnemStart = 6*l + 37
           else:
               print 'error: unknown LC 4,5 qnem suffix', swName[-1]
       #print 'qnemStart', qnemStart
       for i in range(qnemStart, qnemStart+3):
           s = qnemIndexToName(i)
           for pair in ( 'a', 'b' ):
               if s+pair not in names.keys():
                   n = s+pair
                   qnemlid = findLidByName(switchTree, None, n)
                   if qnemlid == None:
                       qnemlid = 'unknown'
                   print 'error:', s+pair, '(lid %d) not in qnem connections from' % qnemlid, swName, '(lid %d) connections' % swLid, names

   # have checked all LC links, so implicitly all qnems uplinks are ok (+/- qnem cable ordering and shelf swaps).

   # examine all qnems
   portmap = {}  # ports by type
   portToM9 = {} # port ranges that go to each m9 LC chip
   # use the 1st qnem a,b as a template ->
   for lr in ( 'a', 'b' ):
       qnem = qnemIndexToName(canonicalQnem) + lr
       qnemlid = findLidByName( switchTree, None, qnem )
       #print 'lr, qnemlid, qnem', lr, qnemlid, qnem
       swName, swLid, a = switchTree[qnemlid]
       assert( swName == qnem )

       portmap[lr] = {}
       portToM9[lr] = {}
       keys = a.keys()
       for p in a.keys():
           name, lid, port = a[p]
           #print p, a[p]
           # find ports that go to LC, nodes, other qnem
           t = lidType( name )
           if t == None:
               # if lidType is confused, assume it's a node
               t = 'node'
           if t not in portmap[lr].keys():
               portmap[lr][t] = []
           portmap[lr][t].append(p)
           if t == "LC":
               m9 = m9number(name)
               if m9 not in portToM9[lr].keys():
                   portToM9[lr][m9] = []
               portToM9[lr][m9].append(p)
   #print portmap
   #print portToM9

   # check that all qnems have the same ports connected to the same m9's. ie. check for cable transposes
   for q in range(1,64):
       for lr in ( 'a', 'b' ):
           qnem = qnemIndexToName(q) + lr
           qnemlid = findLidByName( switchTree, None, qnem )
           #print 'lr, qnemlid, qnem', lr, qnemlid, qnem
           swName, swLid, a = switchTree[qnemlid]
           assert( swName == qnem )

           keys = a.keys()
           for m9 in m9s:
               for p in portToM9[lr][m9]:
                   if p not in a.keys():
                       print qnem, 'port', p, 'does not exist. skipping cable swap check'
                       continue
                   name, lid, port = a[p]
                   if lidType(name) != 'LC':
                       print qnem, 'port', p, 'does not go to a LC'
                   if m9number(name) != m9:
                       print qnem, 'port', p, 'goes to M9-%d' % m9number(name), 'not M9-%d' % m9 + '. suspect cable transpose at qnem end'

   # check that all qnem ports connect to the same LC chip and port (on the different m9's)
   for q in range(1,64):
       for lr in ( 'a', 'b' ):
           qnem = qnemIndexToName(q) + lr
           qnemlid = findLidByName( switchTree, None, qnem )
           #print 'lr, qnemlid, qnem', lr, qnemlid, qnem
           swName, swLid, a = switchTree[qnemlid]
           assert( swName == qnem )

           keys = a.keys()
           lcChip = []
           lcPort = {}
           for p in keys:
               name, lid, port = a[p]
               if lidType(name) != 'LC':
                   continue
               lcChip.append(lcChipName(name))
               m9 = m9number(name)
               if m9 not in lcPort.keys():
                   lcPort[m9] = []
               if port not in lcPort[m9]:
                   lcPort[m9].append(port)
           #print 'lcChip', lcChip
           if len(lcChip) != qnemLcLinks:
               print qnem, 'doesn\'t have', qnemLcLinks, 'links to a M9'
           lcChip.sort()
           if lcChip[0] != lcChip[-1]:
               print qnem, 'doesn\'t connect to the same LC chips on all cables', lcChip
           #print 'lcPort', lcPort
           if len(lcPort.keys()) != len(m9s):  # not 4 m9's on this qnem, but we know that already...
               print qnem, 'doesn\'t connect to', len(m9s), 'm9\'s'
               continue
           # count up how many m9 port sets are the same
           s = {}
           linkDown = 0
           for m in lcPort.keys():
               pts = tuple(lcPort[m])  # list to tuple
               if len(pts) != 3:
                   linkDown = 1
               #print 'pts', pts
               if pts not in s.keys():
                   s[pts] = 1
               else:
                   s[pts] += 1
           #print s
           # skip this test if there is a link down. can't tell much without all links up.
           if not linkDown and len(s.keys()) != 1:   # each of the 4 cables should do the same 3 remote port numbers on an LC, so there should be 1 tuple
               print qnem, 'suspect cable to wrong shelf, qnem side, or port in LC chip. (LC ports):cnt is', s, 'should be ():4.',
               if len(s.keys()) == 2:  # 2,2 or 3,1
                  for p,cnt in s.iteritems():
                     if cnt == 1:  # odd man out in 3,1
                        odd = p
                        for m in lcPort.keys():
                           if odd == tuple(lcPort[m]):
                              print 'odd man out is',
                              for p in a.keys():
                                 name, lid, port = a[p]
                                 if lidType(name) != 'LC':
                                     continue
                                 if m == m9number(name):
                                     print name,
                                     break
               elif len(s.keys()) == 3:  # 2,1,1
                  print 'the two singleton switches are', 
                  for p,cnt in s.iteritems():
                     if cnt == 1:  # odd men out
                        odd = p
                        for m in lcPort.keys():
                           if odd == tuple(lcPort[m]):
                              print 'M9-%s' % m,
               print

   # check that qnems are connected to the right nodes...
   cnt = 0
   for q in range(1,64):
       for lr in ( 'a', 'b' ):
           qnem = qnemIndexToName(q) + lr
           qnemlid = findLidByName( switchTree, None, qnem )
           #print 'lr, qnemlid, qnem', lr, qnemlid, qnem
           swName, swLid, a = switchTree[qnemlid]
           assert( swName == qnem )

           # actually this just counts across qnem nodes ports and assumes the ports numbers are correct and in the right order -  not ideal
           keys = a.keys()
           for p in portmap[lr]['node']:
               cnt += 1
               #print 'cnt', cnt
               if cnt > 1492:
                   continue
               if p not in a.keys():
                   print 'port', p, 'host not on', qnem
                   continue
               name, lid, port = a[p]
               n = 'v%d' % cnt
               if n != name:
                   print 'error node', name, 'is out of order - should be', n

   # check qnem<->qnem
   for q in range(1,64):
       for lr in ( 'a', 'b' ):
           qnem = qnemIndexToName(q) + lr
           qnemlid = findLidByName( switchTree, None, qnem )
           #print 'lr, qnemlid, qnem', lr, qnemlid, qnem
           swName, swLid, a = switchTree[qnemlid]
           assert( swName == qnem )
           m = {}
           keys = a.keys()
           for p in portmap[lr]['qnem']:
              if p not in keys:
                   print 'port', p, 'qnem not on', qnem
                   continue
              n, l, port = a[p]
              if n not in m.keys():
                  m[n] = 0
              m[n] += 1
           if len(m.keys()) != 1:
              print qnem, 'qnem doesn\'t connect to 1 qnem, is', len(m.keys())
              continue
           n = m.keys()[0]
           if m[n] != 12:
              print qnem, 'not 12 qnem<->qnem links. is', m[n]
           if lr == 'a' and n != qnemIndexToName(q) + 'b' or lr == 'b' and n != qnemIndexToName(q) + 'a':
              print qnem, 'other qnem isn\'t right name', n

   # find all M2 chips
   portmap = {}  # lids of uplinks
   m2 = findLidsByType( switchTree, 'leaf' )
   for k in m2:
       swName, swLid, a = switchTree[k]
       if not isM2(swName):
           continue
       portmap[swName] = {}
       keys = a.keys()
       for p in a.keys():
           name, lid, port = a[p]
           #print swName, a[p]
           t = lidType( name )
           #print name, t, lid
           if t == None:
               # if lidType is confused, assume it's a node
               t = 'node'
           if t not in portmap[swName].keys():
               portmap[swName][t] = []
           portmap[swName][t].append((lid,name))
   #print portmap

   # check M2 uplinks are spread across the right LCs
   for k in m2:
       swName, swLid, a = switchTree[k]
       if not isM2(swName):
           continue
       p = portmap[swName]
       #print swName, p
       m = {}
       for l,n in p['LC']:
           name, lid, a = switchTree[l]
           if name not in m.keys():
               m[name] = 0
           m[name] += 1
       #print swName, m
       mm = {}
       for i in m.keys():
           if m[i] != 3:
               print 'not 3 connections from', swName, 'to', i
           mm[i[:4]] = m[i]  # M9-3-LC-5d -> M9-3
       if len(mm.keys()) != 4:
           print swName, 'doesn\'t have uplinks to 4 m9\'s, only', mm.keys()

   # check the connections from hosts to M2's
   # m2-1 has odd marmot + lots, m2-2 even marmot + lots, m2-3 odd hamster, m2-4 even hamster
   for k in m2:
       swName, swLid, a = switchTree[k]
       if not isM2(swName):
           continue
       p = portmap[swName]
       #print swName, p
       if 'node' not in p.keys():  # no up nodes on switch
           continue
       for l,n in p['node']:
           #print swName, n
           base = n.rstrip(string.digits)
           digit = n[len(base):]
           if len(digit) and digit[0] in string.digits:
               digit = int(digit)
           elif len(base) != len(n):
               print 'parsing error'
           if len(base) == len(n):
               #print 'not a numbered node'
               digit = None
           #print base, digit
           if swName == 'M2-4':
               if base != 'hamster' or digit%2 != 0:
                   print swName, 'has a non-even or non-hamster', n
           elif swName == 'M2-3':
               if base != 'hamster' or digit%2 != 1:
                   print swName, 'has a non-odd or non-hamster', n
           elif swName == 'M2-2':
               if base == 'marmot' and digit%2 != 0:
                   print swName, 'has a non-even marmot', n
           elif swName == 'M2-1':
               if base == 'marmot' and digit%2 != 1:
                   print swName, 'has a non-odd marmot', n

   # highly unlikely, but check links between FC's and LC's are ok

   sys.exit(0)
   
   swName, swLid, a = switchTree[lid]
   assert( swLid == lid )
   print 'lid,port', k, 'switch', swName, 'and down port', port, 'is (name, lid, port)', a[port],
   # find the other end
   oSwName, oSwLid, oPort = a[port]

##    # link type could be
##    #   M2 <-> node
##    #   qnem <-> node
##    #   qnem <-> qnem
##    # or either way around of these ->
##    #   qnem <-> LC
##    #   LC <-> FC
##    #   LC <-> M2

##    if oSwLid not in switchTree.keys():
##        assert( lidType(swName) == 'qnem' or lidType(swName) == 'M2' )
##        print lidType(swName) + '<->host',
##        if oSwLid in ignore:
##            print 'lid', lid, 'recently rebooted - ignore',
##        print
##        continue

##    t = lidType(swName)
##    ot = lidType(oSwName)
##    if (t, ot) == ('qnem', 'qnem'):
##        print 'qnem<->qnem'
##        continue

##    if t == 'qnem':
##        assert( ot == 'LC' )
##        print 'qnem<->LC'
##        continue

##    if t == 'FC':
##        assert( ot == 'LC' )
##        print 'FC<->LC'
##        continue

##    if t == 'M2':
##        assert( ot == 'LC' )
##        print 'M2<->LC'
##        continue

##    if t == 'LC':
##        if ot == 'M2':
##            print 'LC<->M2'
##        elif ot == 'FC':
##            print 'LC<->FC'
##        elif ot == 'qnem':
##            print 'LC<->qnem'
##        else:
##            print 'LC connected to unknown - illegal link?'
##            exit(1)
