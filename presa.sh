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

if test -f /tmp/presa.status && test "$(cat /tmp/presa.status)" = manual; then
  state=manual
else
  # default all'avvio: presa controllata automaticamente
  state=$(get $TOPIC/state | jq -r .state)
fi
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
    wait|discharging) state=night ;;
    *)
      net=$(get pv/production/available | sed 's,\..*,,')
      home=$(get pv/home | sed 's,\..*,,')
      switch=$(get $TOPIC/state | jq -r .state)

      # rileva accensione e spegnimento manuale
      if test $switch = on && test $state = off; then
        state=manual
        on=1
        off=0
      fi
      if test $switch = off && test $state = on; then
        state=off
        on=0
        off=1
      fi

      # azioni automatiche
      case $state in
        off)
          # spento, aspetta dieci minuti prima di riaccendere
          # in caso di spegnimento manuale, lascia il tempo di staccare
          if test $off -gt 10 && test $net -ge $thres && test $home -lt 1400; then
            put $TOPIC/switch.turn_on
            state=on
            switch=on
          fi
          ;;
        night|on)
          # nuovo giorno => spegne alla mattina
          # acceso automaticamente => carica per almeno un'ora
          if test $state = night || (test $on -gt 60 && test $net -le 1150); then
            put $TOPIC/switch.turn_off
            state=off
            switch=off
          fi
          ;;
        manual)
          # si spegne solo la mattina dopo, passando da night
          ;;
      esac

      if test $switch = on; then
        on=$(($on + 1))
        off=0
      else
        off=$(($off + 1))
        on=0
      fi

      echo "$(date) $mode | input $net | state $state | on $on off $off" > /tmp/presa.log
      echo "$state" > /tmp/presa.status
      ;;
  esac
  sleep 59
done
