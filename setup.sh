#!/bin/bash

ODOO_VERSION=${ODOO_VERSION:-19.0}

BASE_DIR="../../Sources/$ODOO_VERSION"

echo "Clonando Odoo versión $ODOO_VERSION en $BASE_DIR..."

mkdir -p "$BASE_DIR"

git clone --depth=1 --single-branch --branch=$ODOO_VERSION \
    https://github.com/odoo/odoo.git "$BASE_DIR/odoo"

git clone --depth=1 --single-branch --branch=$ODOO_VERSION \
    https://github.com/odoo/enterprise.git "$BASE_DIR/enterprise"

echo "Clonación completada en:"
echo "$BASE_DIR/odoo"
echo "$BASE_DIR/enterprise"