#!/bin/sh

./dev.sh exec -u root odoo odoo scaffold $@
./dev.sh exec -u root odoo mv /$@ /mnt/extra-addons/
./dev.sh exec -u root odoo chown -R ubuntu:ubuntu /mnt/extra-addons/$@
./dev.sh exec -u root odoo ls -l /mnt/extra-addons/