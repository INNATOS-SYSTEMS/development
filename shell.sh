#!/usr/bin/bash

sudo docker-compose exec odoo odoo shell -c /etc/odoo/odoo.conf --addons-path /mnt/enterprise,/mnt/extra-addons --db_host db -r [DB_USER] -w [DB_PASSWORD] -d [DB_NAME]