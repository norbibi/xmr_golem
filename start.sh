#!/bin/bash

sleep 10
sshpass -p $1 ssh -R 5555:xmrpool.eu:5555 -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no root@127.0.0.1 -p $2 > /dev/null 2>&1