#!/usr/bin/env python

# find and print nodes that have been rebooted since the last ibclearerrors
# used by IB error checking scripts to eliminate nodes rebooted during that interval from error sweeps

from ibTracePorts import findMostRecentFile
from ibFlagErrors import uptimes, filterHosts
from hms import hms
import time, sys

ibDir = '/root/ib'
suffix = 'ibclearerrors'

f, fTime = findMostRecentFile( ibDir, suffix )
#print 'using', f, 'for errors, time', fTime, 'now', time.time(), 'diff', time.time() - fTime, 'hrs', (time.time() - fTime)/3600.0

uptime, down = uptimes()
if uptime == None:  # failed
    sys.exit(1)

#print 'len(uptime)', len(uptime) #, 'uptime', uptime   # uptimes by hostname
#print 'len(down)', len(down), 'down', down

ignore = filterHosts( uptime, fTime )
ignore.sort()
#print 'recently rebooted - ignore hosts', ignore, 'len', len(ignore)

print '# nodes rebooted in last', hms(time.time() - fTime)
for i in ignore:
    if i in down:
        continue
    print i

# no idea about down nodes, so assume they're evil
print '# down'
for i in down:
    print i
