#!/usr/bin/env python3
# simple boardd wrapper that updates the panda first
import os
import time
import subprocess
from typing import List
from functools import cmp_to_key

from panda import BASEDIR as DEFAULT_FW_FN, DEFAULT_H7_FW_FN, Panda, PandaDFU
from common.basedir import BASEDIR
from common.params import Params
from selfdrive.hardware import TICI
from selfdrive.swaglog import cloudlog

INTERNAL_TYPES = [Panda.HW_TYPE_UNO, Panda.HW_TYPE_DOS]


def get_expected_signature(panda : Panda) -> bytes:
  fn = DEFAULT_H7_FW_FN if (panda._mcu_type == 2) else DEFAULT_FW_FN

  try:
    return Panda.get_signature_from_firmware(fn)
  except Exception:
    cloudlog.exception("Error computing expected signature")
    return b""


def flash_panda(panda_serial : str) -> Panda:
  panda = Panda(panda_serial)

  fw_signature = get_expected_signature(panda)

  panda_version = "bootstub" if panda.bootstub else panda.get_version()
  panda_signature = b"" if panda.bootstub else panda.get_signature()
  cloudlog.warning(f"Panda %s connected, version: %s, signature %s, expected %s" % (
    panda_serial,
    panda_version,
    panda_signature.hex()[:16],
    fw_signature.hex()[:16],
  ))

  if panda.bootstub or panda_signature != fw_signature:
    cloudlog.info("Panda firmware out of date, update required")
    panda.flash()
    cloudlog.info("Done flashing")

  if panda.bootstub:
    bootstub_version = panda.get_version()
    cloudlog.info(f"Flashed firmware not booting, flashing development bootloader. Bootstub version: {bootstub_version}")
    panda.recover()
    cloudlog.info("Done flashing bootloader")

  if panda.bootstub:
    cloudlog.info("Panda still not booting, exiting")
    raise AssertionError

  panda_signature = panda.get_signature()
  if panda_signature != fw_signature:
    cloudlog.info("Version mismatch after flashing, exiting")
    raise AssertionError

  return panda


def get_pandas() -> List[Panda]:
  panda = None
  panda_dfu = None

  cloudlog.info("Connecting to panda")

  # Flash all Pandas in DFU mode
  for p in PandaDFU.list():
    cloudlog.info(f"Panda in DFU mode found, flashing recovery {p}")
    panda_dfu = PandaDFU(p)
    panda_dfu.recover()
    time.sleep(1)

  # Ensure we have at least one panda
  pandas : List[str] = []
  while not pandas:
    pandas = Panda.list()

    if not pandas:
      time.sleep(1)

  cloudlog.info(f"{len(pandas)} panda(s) found, connecting - {pandas}")

  # Flash pandas
  r = []
  for serial in pandas:
    r.append(flash_panda(serial))

  return r

def panda_sort_cmp(a : Panda, b : Panda):
  a_type = a.get_type()
  b_type = b.get_type()

  # make sure the internal one is always first
  if (a_type in INTERNAL_TYPES) and (b_type not in INTERNAL_TYPES):
    return -1
  if (a_type not in INTERNAL_TYPES) and (b_type in INTERNAL_TYPES):
    return 1

  # sort by hardware type
  if a_type != b_type:
    return a_type < b_type
  
  # last resort: sort by serial number
  return a._serial < b._serial

def main() -> None:
  while True:
    pandas = get_pandas()

    # check health for lost heartbeat
    for panda in pandas:
      health = panda.health()
      if health["heartbeat_lost"]:
        Params().put_bool("PandaHeartbeatLost", True)
        cloudlog.event("heartbeat lost", deviceState=health, serial=panda._serial)

      cloudlog.info(f"Resetting panda {panda._serial}")
      panda.reset()

    # sort pandas to have deterministic order
    pandas.sort(key=cmp_to_key(panda_sort_cmp))
    panda_serials = list(map(lambda p: p._serial, pandas))

    # close all pandas
    for p in pandas:
      p.close()

    # run boardd with all connected serials as arguments
    os.chdir(os.path.join(BASEDIR, "selfdrive/boardd"))
    subprocess.run(["./boardd", *panda_serials], check=True)

if __name__ == "__main__":
  main()
