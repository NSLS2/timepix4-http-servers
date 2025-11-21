#!/bin/sh

libdir="/home/timepix4/master_updated/"
export PYTHONPATH=$libdir

cd $libdir
python3 ~/timepix4-server.py
