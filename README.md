# My Home Assistant backend scripts

## Contents

* `ensolar2.*`: scripts to interact with solar roof inverter via Modbus RTU

* `ha-mqtt-gateway.*`: scripts for two-way interaction with Home Assistant via MQRR

* `mosquitto.conf`: drop-in file for `/etc/mosquitto/conf.d`

* `presa.*`: script to control HA switch based on solar roof production

* `analysis/`: scripts to analyze data logged by ensolar2.py

* `old/`: scripts I don't use anymore

## Coming next

* Ansible playbook to install everything
