#!/usr/bin/env python

# (c) Robin Humble 2011
# licensed under the GPL v3

# slurp up ibnetdiscover and print the lids and ports of a particular cable

import os, sys, string
from ibTracePorts import parseIbnetdiscover
from ibFlagErrors import lidType
from ibCheckTopology import findLidByName

ibDir = '/root/ib'

verbose=0
if '-v' in sys.argv:
   sys.argv.remove('-v')
   verbose=1

qnemTraces=0
if '-q' in sys.argv:
   sys.argv.remove('-q')
   qnemTraces=1

def usage():
   print 'usage:', sys.argv[0], '[-v] [-q] switchName1 switchName2'
   print ' output is lid,port\'s on switchName1 that connect to switchName2'
   print '   -v will print name,lid,port at both ends'
   print '   -q will print qnem<->qnem traces as well as cabled connections'
   print ' eg.', sys.argv[0], 'qnem-07-4a M9-4-LC-0c'
   sys.exit(1)

if len(sys.argv) > 3:
   usage()

if __name__ == '__main__':
   switchTree, byName, lph, r = parseIbnetdiscover( ibDir=ibDir )
   #print 'switchTree (len', len(switchTree), ')' # , switchTree  # by switch LID
   #print 'byName (len', len(byName), ')' # , byName  # by hostname
   #print 'lph (len', len(lph), ')' # , lph  # swlid, swport, lid, host

   s1 = sys.argv[1]
   s2 = sys.argv[2]
   if lidType(s2) == 'qnem' and lidType(s1) != 'qnem':
      # swap them to put the qnem first
      s = s1
      s1 = s2
      s2 = s

   l1 = findLidByName( switchTree, lph, s1 )
   if l1 == None:
      print 'Error: could not find s1', s1, 'in switchTree. please check switch name'
      sys.exit(1)
   l2 = findLidByName( switchTree, lph, s2 )
   if l2 == None:
      print 'Error: could not find s2', s2, 'in switchTree. please check switch name'
      sys.exit(1)

   swName1, swLid1, a1 = switchTree[l1]
   swName2, swLid2, a2 = switchTree[l2]
   assert(swName1 == s1)
   assert(swName2 == s2)

   # see if we're doing qnem<->qnem
   qq = ( lidType(swName1) == 'qnem' and lidType(swName2) == 'qnem' )

   for p in a1.keys():
      name, lid, port = a1[p]
      if name == swName2:
         if qnemTraces == 0 and qq and port > 15:  # qnem<->qnem cables are on ports 1,2,3 and 13,14,15
            continue
         if verbose:
            print swName1, swLid1, p, '<->', name, lid, port
         else:
            print swLid1, p
