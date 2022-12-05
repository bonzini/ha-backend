#! /bin/sh
HOST=127.0.0.1
TOPIC=ha-mqtt-gateway/switch.0xa4c1383a449b4c99_switch
set -xe
get() {
  mosquitto_sub -h $HOST -t $1 -C 1
  #echo -n $1'> ' >&2; read x; echo $x
}
put() {
  mosquitto_pub -h $HOST -t $1 -m "$2"
  #echo $1=$2
}
on=100
off=100
while :; do
  month=$(date +%m)
  case $month in
     12) thres=1150 ;;
     01|11) thres=1250 ;;
     02|10) thres=1450 ;;
     03|09) thres=1600 ;;
     04|08) thres=1800 ;;
     05|06|07) thres=1900 ;;
  esac

  mode=$(get pv/mode)
  case $mode in
    wait|discharging) ;;
    *)
      net=$(get pv/production/available | sed 's,\..*,,')
      home=$(get pv/home | sed 's,\..*,,')
      state=$(get $TOPIC/state | jq -r .state)
      if test $off -gt 10 && test $net -ge $thres && test $home -lt 1400; then
        put $TOPIC/switch.turn_on
        state=on
      fi
      if test $on -gt 60 && test $net -le 1150; then
        put $TOPIC/switch.turn_off
        state=off
      fi
      if test $state = on; then
        on=$(($on + 1))
        off=0
      else
        off=$(($off + 1))
        on=0
      fi
      echo "$(date) $mode | input $net | state $state | on $on off $off" > /tmp/presa.log
      ;;
  esac
  sleep 59
done
