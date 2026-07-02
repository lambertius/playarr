# -*- coding: utf-8 -*-
"""
Playarr — Kodi add-on entry point.

Kodi launches this file for every navigation request, passing:
  sys.argv[0]  base plugin URL  (plugin://plugin.video.playarr/)
  sys.argv[1]  handle           (int, used by xbmcplugin calls)
  sys.argv[2]  query string     (?action=...&...)

All real logic lives in resources/lib/addon.py.
"""
import sys

from resources.lib.addon import run

if __name__ == "__main__":
    run(sys.argv)
