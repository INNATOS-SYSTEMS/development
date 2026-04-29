FROM odoo:19

# Change to root user to prepare image
USER root

# Add enterprise modules
COPY ${ENTERPRISE_PATH} /mnt/enterprise

# Add custom modules
COPY ./extra /mnt/extra-addons

# Fix modules permisions
RUN chown -R odoo /mnt/*

# Change to odoo user to run odoo instance
USER odoo
