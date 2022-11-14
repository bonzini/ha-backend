#! /bin/sh

filter='cat'
d=2018-08-02
while [ "$d" != 2019-08-01 ]; do 
  echo $d >&2
  f=$(date '+%Y%m%d' -d "$d")
  d=$(date -I -d "$d + 1 day")
  curl "$@" 'http://192.168.10.236/api/data/daily?d='$f | $filter
  filter='sed 1d'
done > pv.csv
awk 'BEGIN { FS = ","; OFS = "\t"; delta = -86400; print "TS", "H", "M", "WD", "DELTA", "BUY", "SELL", "PV", "CHG", "DIS", "HOME", "OVERHEAD" } NR > 1 {
  $1 += 7200000
  h = int($1/3600000) % 24
  m = int($1/60000) % 60
  wd = (int($1/86400000) - 4) % 7
  ts = int($1/86400000) * 86400
  if (h == 0) {
      delta = -86400
  }
  invw = $(20)
  net = $(97)
  pv = $(98)
  bt = $(99)
  load = $(101)
  buy = sell = 0
  dis = chg = 0
  if (net > 0) buy = net; else sell = -net;
  if (bt > 0) chg = bt; else dis = -bt;
  overhead = invw <= 0 ? -invw : dis ? load - $(102) : (buy + pv) - (sell + load + chg)
  w_in = buy + (dis ? dis : pv)
  w_out = sell + chg + overhead
  home = w_in - w_out
  if (pv > 0) {
      delta = 0
  }
  print ts, h, m, wd, delta, buy, sell, pv, chg, dis, home, overhead
}' pv.csv > pvclean.csv
awk 'BEGIN { OFS = "\t"; first = 1; print "TS", "H", "WD", "BUY", "SELL", "PV", "CHG", "DIS", "HOME", "OVERHEAD" } NR > 1 {
  if ($1 + $5 != last || $2 != last_h) {
      if (!first) {
          print last, last_h, last_wd, buy, sell, pv, chg, dis, home, overhead
      }
      last = $1 + $5
      last_h = $2
      last_wd = $4
      if (!$5) {
        first = 0
      }
      buy = sell = pv = chg = dis = home = overhead = 0
  }
  buy += $6 / 1000 / 12
  sell += $7 / 1000 / 12
  pv += $8 / 1000 / 12
  chg += $9 / 1000 / 12
  dis += $(10) / 1000 / 12
  home += $(11) / 1000 / 12
  overhead += $(12) / 1000 / 12
}
END {
  print last, last_h, last_wd, buy, sell, pv, chg, dis, home, overhead
}
' pvclean.csv > pvhourly.csv
awk 'BEGIN { OFS = "\t"; first = 1; print "TS", "WD", "BUY", "SELL", "PV", "CHG", "DIS", "HOME", "OVERHEAD" } NR > 1 {
  if ($1 != last) {
      if (!first) {
          print last, last_wd, buy, sell, pv, chg, dis, home, overhead
      }
      last = $1
      last_wd = $3
      first = 0
      buy = sell = pv = chg = dis = home = overhead = 0
  }
  buy += $4
  sell += $5
  pv += $6
  chg += $7
  dis += $8
  home += $9
  overhead += $(10)
}
END {
  print last, last_wd, buy, sell, pv, chg, dis, home, overhead
}
' pvhourly.csv > pvdaily.csv
awk 'BEGIN { OFS = "\t"; print "WD", "H", "BUY", "SELL", "DIS", "HOME" } NR > 1 {
  idx = $3 "\t" $2
  if (!first) {
      buy[idx] += $4
      sell[idx] += $5
      dis[idx] += $8
      home[idx] += $9
  }
}
END {
  for (wd = 0; wd < 7; wd++)
    for (h = 0; h < 24; h++) {
      idx = wd "\t" h
      print wd, h, buy[idx], sell[idx], dis[idx], home[idx]
    }
}
' pvhourly.csv > pvhourwd.csv
