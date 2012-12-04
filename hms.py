#!/usr/bin/env python

def hms( t ):
   h = int(t/3600)
   m = int((t - h*3600)/60)
   s = t - h*3600 - m*60
   return '%2d:%02d:%02d' % ( h, m, s )
