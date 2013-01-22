#!/usr/bin/env python

# (c) Robin Humble 2010
# licensed under the GPL v3

# slurp up ibnetdiscover and check the topology of all links

import os, sys, string
from ibTracePorts import parseIbnetdiscover
from ibFlagErrors import lidType

ibDir = '/home/rjh'
ibDir = '/root/ib'


def uniq( list ):
    l = []
    prev = None
    for i in list:
        if i != prev:
            l.append( i )
        prev = i
    return l

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

def leafIndexToName( i ):
    s = 'ib%.3d...' % i
    return s

def coreNumber( name ):
    # convert eg. MF0;sx6536-1a:SXX536/L31/U1 to '1'
    return int(name.split('-')[1][0])

def lcChipNumber( name ):
    # convert eg. MF0;sx6536-1a:SXX536/L31/U1 to '31'
    assert( name.split('/')[1][0] == 'L' )
    return int(name.split('/')[1][1:])

def isInfraSwitch( name ):
    # infra switches are named ib301-ib308
    assert( name[:2] == 'ib' )
    swNum = int(name[2:5])
    if swNum > 300 and swNum < 309:
        return 1
    return 0

def nodeNumToName(i):
    return 'r%.4d' % i

def nodeNameToNum(name):
    return int(name.split()[0][1:])

if __name__ == '__main__':
   if len(sys.argv) == 2:
       switchTree, byName, lph, r = parseIbnetdiscover( ibNetFile=sys.argv[1] )
   else:
       switchTree, byName, lph, r = parseIbnetdiscover( ibDir=ibDir )
   print 'switchTree (len', len(switchTree), ')' # , switchTree  # by switch LID
   print 'byName (len', len(byName), ')' # , byName  # by hostname
   print 'lph (len', len(lph), ')' # , lph  # swlid, swport, lid, host

   canonicalLeaf = 1  # leaf switch to use as a template for connectivity pattern of all
   chipPorts = 36
   lcFcLinks = chipPorts/2    # full fat tree
   lcLeafLinks = chipPorts/2  # full fat tree
   lcM2Links = 18
   cores = ( 1,2,3,4,5,6 )
   leafLcLinks = 18   # can be eg. 12 on a qnem: 12 to nodes, 12 to LC, 12 to other qnem
   numNodes = 3592
   nodesPerLeaf = 18

   numOss = 50
   numMds = 6
   ossName = 'lemming'
   mdsName = 'gerbil'

   # leaf switch indices
   leafs = range(1,1+200)
   leafs.extend(range(301,301+8))

   # find all LC chips
   lc = findLidsByType( switchTree, 'LC' )
   for k in lc:
       swName, swLid, a = switchTree[k]
       fc = []
       q = []
       keys = a.keys()
       #if len(keys) != chipPorts:
       #    print 'error:', len(keys), 'links from LC', swName, 'should be', chipPorts

       # compute:
       #   lc 1 is connected to ib 1-6
       #   lc 2   ""    ""         7-12
       #   ...
       #   lc 33  ""    ""         193-198
       #   lc 34  ""    ""         199-200

       # infra:
       #   lc 34  ""    ""         301-304
       #   lc 35  ""    ""         305-308  (only 4 leafs on this lc (12 ports used))

       # spare:
       #   lc 35  - 6 ports free
       #   lc 36  - not present

       l = lcChipNumber( swName )
       if l < 34:
           lStart = 1 + (l-1)*6
           leafsOnLc = range(lStart, lStart+6)
       elif l == 34:  # line card 34 has some infra on it as well as compute
           leafsOnLc = [199,200]
           leafsOnLc.extend(range(301,301+4))
       elif l == 35:  # line card 35 is partially populated with infra
           leafsOnLc = range(305,305+4)
       else:
           print 'unknown leaf chip', swName, 'number', l

       expectLinks = 3*len(leafsOnLc)

       for p in a.keys():
           name, lid, port = a[p]
           t = lidType( name )
           if t == 'FC':
               fc.append(p)
           elif t == 'leaf':
               q.append(p)
           else:
               print 'unknown/illegal link on', swName, 'port', p, 'is type', t, '(details', a[p], ')'

       if len(fc) != lcFcLinks:
           print 'error:', len(fc), 'links from LC', swName, 'to FCs should be', lcFcLinks
       if len(q) != expectLinks:
           print 'error:', len(q), 'links from LC', swName, 'to leafs should be', expectLinks

       ports = a.keys()
       ports.sort()
       #print swName, 'ports', ports
       #print 'q', q

       if not len(q):  # handle a line card with nothing plugged into it
           continue

       # check they are connected to the correct leafs in the correct order
       # each LC should go to 6 leaf chips
       names = {}
       for p in q:
           name, lid, port = a[p]
           if name not in names:
               names[name] = 1
           else:
               names[name] += 1
       if len(names.keys()) != 6 and l != 35:  # last line card isn't fully populated
           print 'error: LC', swName, 'doesn\'t go to 6 leaf chips'
       for n, cnt in names.iteritems():
           if cnt != 3:
               print 'error: LC', swName, 'hasn\'t got 3 links to', n, ', has', cnt
       #print names

       for i in leafsOnLc:
           s = leafIndexToName(i)
           if s not in names.keys():
               leaflid = findLidByName(switchTree, None, s)
               if leaflid == None:
                   leaflid = 'unknown'
               print 'error:', s, '(lid %s) not in leaf connections from' % str(leaflid), swName, '(lid %d) connections' % swLid, names

   # have checked all LC links, so implicitly all leafs uplinks are ok (+/- leaf cable ordering and shelf swaps).

   # examine all leafs
   portmap = {}  # ports by type
   portToCore = {} # port ranges that go to each core LC chip
   # use one leaf chip as a template ->
   leaf = leafIndexToName(canonicalLeaf)
   leaflid = findLidByName( switchTree, None, leaf )
   #print 'lr, leaflid, leaf', lr, leaflid, leaf
   swName, swLid, a = switchTree[leaflid]
   assert( swName == leaf )
   portmap = {}
   portToCore = {}
   portToNodeNum = {}
   minNode = numNodes + 666
   keys = a.keys()
   for p in a.keys():
       name, lid, port = a[p]
       #print p, a[p]
       # find ports that go to LC, nodes, other leaf
       t = lidType( name )
       if t == None:
           # if lidType is confused, assume it's a node
           t = 'node'
       if t not in portmap.keys():
           portmap[t] = []
       portmap[t].append(p)
       if t == 'LC':
           core = coreNumber(name)
           if core not in portToCore.keys():
               portToCore[core] = []
           portToCore[core].append(p)
       elif t == 'node':
           i = nodeNameToNum(name)
           minNode = min(i, minNode)
           portToNodeNum[p] = i
   #print portmap
   #print portToCore
   #print portToNodeNum
   if minNode != 1:
       print 'shifting canonical node number mapping on shelf so it starts from 1, not', minNode
       for i in portToNodeNum.keys():
           portToNodeNum[i] = portToNodeNum[i] - minNode + 1
       #print portToNodeNum

   # check that all leafs have the same ports connected to the same core's. ie. check for cable transposes
   for q in leafs:
       leaf = leafIndexToName(q)
       leaflid = findLidByName( switchTree, None, leaf )
       #print 'leaflid, leaf', leaflid, leaf
       if leaflid == None:
           print 'unknown leaf', leaf
           continue
       swName, swLid, a = switchTree[leaflid]
       assert( swName == leaf )

       keys = a.keys()
       for core in cores:
           for p in portToCore[core]:
               if p not in a.keys():
                   print leaf, 'port', p, 'does not exist. skipping cable swap check'
                   continue
               name, lid, port = a[p]
               if lidType(name) != 'LC':
                   print leaf, 'port', p, 'does not go to a LC'
               if coreNumber(name) != core:
                   print leaf, 'port', p, 'goes to core switch %d' % coreNumber(name), 'not core switch %d' % core + '. suspect cable transpose at leaf end'

   # check that all leaf ports connect to the same LC chip and port (on the different core's)
   for q in leafs:
       leaf = leafIndexToName(q)
       leaflid = findLidByName( switchTree, None, leaf )
       #print 'leaflid, leaf', leaflid, leaf
       if leaflid == None:
           print 'unknown leaf', leaf
           continue
       swName, swLid, a = switchTree[leaflid]
       assert( swName == leaf )

       keys = a.keys()
       lcChip = []
       lcPort = {}
       for p in keys:
           name, lid, port = a[p]
           if lidType(name) != 'LC':
               continue
           lcChip.append(lcChipNumber(name))
           core = coreNumber(name)
           if core not in lcPort.keys():
               lcPort[core] = []
           if port not in lcPort[core]:
               lcPort[core].append(port)
       #print 'lcChip', lcChip
       if len(lcChip) != leafLcLinks:
           print leaf, 'doesn\'t have', leafLcLinks, 'links to a core switch'
       lcChip.sort()
       if lcChip[0] != lcChip[-1]:
           print leaf, 'doesn\'t connect to the same LC chips on all cables', lcChip
       #print 'lcPort', lcPort
       if len(lcPort.keys()) != len(cores):  # not 6 core's on this leaf, but we know that already...
           print leaf, 'doesn\'t connect to', len(cores), 'core\'s'
           continue
       # count up how many core port sets are the same
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
       if not linkDown and len(s.keys()) != 1:   # each of the 6 cables should do the same 3 remote port numbers on an LC, so there should be 1 tuple
           print leaf, 'suspect cable to wrong shelf, leaf side, or port in LC chip. (LC ports):cnt is', s, 'should be ():6.',
           if len(s.keys()) == 2:  # could be 4,2 or 5,1 or 3,3
               for p,cnt in s.iteritems():
                   if cnt == 1:  # odd man out in 5,1
                       odd = p
                       for m in lcPort.keys():
                           if odd == tuple(lcPort[m]):
                               print 'odd man out is',
                               for p in a.keys():
                                   name, lid, port = a[p]
                                   if lidType(name) != 'LC':
                                       continue
                                   if m == coreNumber(name):
                                       print name,
                                       break
           # for 6 core switches, == 3 could be 3,2,1 or 4,1,1 or 2,2,2 ... too hard to diagnose
           print

   # check that leafs are connected to the right nodes...
   cnt = 0
   for q in leafs:
       if q > 300:  # infra nodes are handled separately below
           continue
       leaf = leafIndexToName(q)
       leaflid = findLidByName( switchTree, None, leaf )
       #print 'leaflid, leaf', leaflid, leaf
       if leaflid == None:
           print 'unknown leaf', leaf
           continue
       swName, swLid, a = switchTree[leaflid]
       assert( swName == leaf )

       keys = a.keys()
       chipStart = (q-1)*nodesPerLeaf
       for p in portmap['node']:
           nodeNum = chipStart + portToNodeNum[p]
           if nodeNum > numNodes:
               continue
           n = nodeNumToName(nodeNum)
           if p not in a.keys():
               print 'node', n, 'not on', leaf, 'port', p
               continue
           name, lid, port = a[p]
           if n != name.split()[0]:  # trim off HCA-*
               print 'error node', name.split()[0], 'is out of order - should be', n

   # check leaf<->leaf
   #   - not applicable here - see ibCheckTopology.Sun.py


   # find all infra chips
   portmap = {}  # lids of uplinks
   m2 = findLidsByType( switchTree, 'leaf' )
   for k in m2:
       swName, swLid, a = switchTree[k]
       if not isInfraSwitch(swName):
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
       if not isInfraSwitch(swName):
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
           mm[coreNumber(i)] = m[i]
       if len(mm.keys()) != 6:
           print swName, 'doesn\'t have uplinks to 6 core switches, only', mm.keys()

   # check the connections from hosts to M2's
   # make sure lemming and gerbil pairs are separated, and all lemmings are separate from all gerbils
   oss_mds = {}
   for k in m2:
       swName, swLid, a = switchTree[k]
       if not isInfraSwitch(swName):
           continue
       p = portmap[swName]
       #print swName, p
       if 'node' not in p.keys():  # no up nodes on switch
           continue
       for l,n in p['node']:
           #print swName, n
           #  n is eg. lemming1 HCA-1 port2
           host = n.split()[0]
           base = host.rstrip(string.digits)
           digit = host[len(base):]
           if len(digit) and digit[0] in string.digits:
               digit = int(digit)
           elif len(base) != len(host):
               print 'parsing error'
           if len(base) == len(host):
               #print 'not a numbered node'
               digit = None
           #print base, digit
           oss_mds[n] = swName

   # check for oss/mds pairs on the same switch
   for k in ( ('mds', mdsName, numMds), ('oss', ossName, numOss) ):
       t, base, num = k
       for j in ( '', ' port2' ):
           for i in range(1,num+1,2):
               n = base + '%d HCA-1' % i + j
               twin = base + '%d HCA-1' % (i+1) + j
               if n not in oss_mds.keys():
                   print n, 'not found'
                   continue
               if twin not in oss_mds.keys():
                   print twin, 'not found'
                   continue
               if oss_mds[n] == oss_mds[twin]:
                   print t + ' pair', n,twin, 'are on the same switch', oss_mds[n]

   # check for primary and failover oss/mds connections on the same switch
   for k in ( ('mds', mdsName, numMds), ('oss', ossName, numOss) ):
       t, base, num = k
       for i in range(1,num+1):
           n1 = base + '%d HCA-1' % i
           n2 = base + '%d HCA-1' % i + ' port2'
           if n1 not in oss_mds.keys():
               print n1, 'not found'
               continue
           if n2 not in oss_mds.keys():
               print n2, 'not found'
               continue
           if oss_mds[n1] == oss_mds[n2]:
               print t + ' pair', n1,n2, 'are on the same switch', oss_mds[n1]

   # check for mix of oss/mds on the same switches
   ossSw = []
   mdsSw = []
   for k in ( ('mds', mdsName, numMds, mdsSw), ('oss', ossName, numOss, ossSw) ):
       t, base, num, l = k
       for i in range(1,num+1):
           for j in ( '', ' port2' ):
               n = base + '%d HCA-1' % i + j
               if n not in oss_mds.keys():
                   continue
               l.append(oss_mds[n])
   ossSw.sort()
   mdsSw.sort()
   ossSw = uniq(ossSw)
   mdsSw = uniq(mdsSw)
   #print 'oss sw', ossSw
   #print 'mds sw', mdsSw

   for i in range(1,numOss+1):
       for j in ( '', ' port2' ):
           n = ossName + '%d HCA-1' % i + j
           if n not in oss_mds.keys():
               continue
           sw = oss_mds[n]
           if sw in mdsSw:
               print n, 'is on a switch with mds nodes'

   for i in range(1,numMds+1):
       for j in ( '', ' port2' ):
           n = mdsName + '%d HCA-1' % i + j
           if n not in oss_mds.keys():
               continue
           sw = oss_mds[n]
           if sw in ossSw:
               print n, 'is on a switch with oss nodes'

   # highly unlikely, but could check links between FC's and LC's are ok too?

   sys.exit(0)
