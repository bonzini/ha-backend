#! /bin/sh

# superseded by ensolar2.py

#exec > /dev/null
#exec 2> /dev/null

curl_api() {
  api=$1
  shift
  curl "http://192.168.10.236/api/$api" \
    -H 'Origin: http://192.168.10.236' \
    -H 'Referer: http://192.168.10.236/' "$@"
}

login() {
  curl_api login -H 'Content-Type: application/json;charset=utf-8' \
     --data-raw '{"username":"admin","password":"ensolar"}' | jq -r .token
}

get_dash() {
  curl_api dash -H "Authorization: Bearer $1" | \
    jq -r 'to_entries | map([.key, .value|tostring]) | map(join("\t")) | join("\n")'
}

token=$(login)
get_dash $token | (while read key value; do
  eval $key=$value
done

if test ${XPV%.*} = 0 && test ${XBT%.*} -lt 0; then
  mode=discharging
elif test ${XPV%.*} = 0; then
  mode=wait
elif test ${XBT%.*} -lt 100; then
  mode=day
else
  mode=charging
fi

case "$XBT" in
  (-*) discharge=${XBT#-}; charge=0 ;;
  (*) discharge=0; charge=$XBT ;;
esac
case "$XGR" in
  (-*) balance=sell; sell=${XGR#-}; buy=0 ;;
  (*) balance=buy; sell=0; buy=$XGR ;;
esac

case "$INVW:$discharge" in
  (-*:*) overhead=${INVW#-} ;;
  (*:0) overhead=$(( ${XPV%.*} + ${XGR%.*} - ${XHOME%.*} - ${XBT%.*} )) ;;
  (*:*) overhead=$(( ${XHOME%.*} - ${XAUTO%.*} )) ;;
esac

mosquitto_pub -h 192.168.10.210 -t pv/mode -r -m $mode
mosquitto_pub -h 192.168.10.210 -t pv/balance -r -m $balance
mosquitto_pub -h 192.168.10.210 -t pv/home -r -m $XHOME
mosquitto_pub -h 192.168.10.210 -t pv/overhead -r -m $overhead
mosquitto_pub -h 192.168.10.210 -t pv/production -r -m $XPV
mosquitto_pub -h 192.168.10.210 -t pv/production/available -r -m $((${XPV%.*} - ${XBT%.*}))
mosquitto_pub -h 192.168.10.210 -t pv/sell -r -m $sell
mosquitto_pub -h 192.168.10.210 -t pv/buy -r -m $buy
mosquitto_pub -h 192.168.10.210 -t pv/bat/charge -r -m $charge
mosquitto_pub -h 192.168.10.210 -t pv/bat/discharge -r -m $discharge )
