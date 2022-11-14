#! /bin/sh
case "$0" in
  */*) exec=$0; ;;
  *) exec=`command -v $0` ;;
esac

path=`dirname $exec` 
. $path/bin/activate
exec $path/ensolar2.py "$@"
