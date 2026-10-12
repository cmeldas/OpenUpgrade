"""Encode any known changes to the database here
to help the matching process
"""

# Renamed modules is a mapping from old module name to new module name
renamed_modules = {
    # odoo
    # point_of_sale depends on iot_webserial instead of iot_base, which had no
    # data (static assets only)
    "iot_base": "iot_webserial",
    "website_sale_autocomplete": "website_address_autocomplete",
    # odoo/enterprise
    # OCA/...
}

# Merged modules contain a mapping from old module names to other,
# preexisting module names
merged_modules = {
    # odoo
    # GLN is now an additional identifier of res.partner (account)
    "account_add_gln": "account",
    # IBAN validation of res.partner.bank is in base
    "base_iban": "base",
    # VAT validation is in base, vat_check_vies in account, the VIES
    # online check in the new module l10n_eu_account_vies (see split_modules)
    "base_vat": "base",
    "hr_homeworking": "hr",
    "hr_hourly_cost": "hr",
    "hr_org_chart": "hr",
    # theme_common has no successor; theme_* modules depend on website only
    "theme_common": "website",
    "website_sale_collect_wishlist": "website_sale_collect",
    "website_sale_comparison": "website_sale",
    "website_sale_comparison_wishlist": "website_sale",
    "website_sale_stock_wishlist": "website_sale",
    "website_sale_wishlist": "website_sale",
    # odoo/enterprise
    # OCA/...
}

# Split modules: when the module on the left was installed in the previous
# version (according to openupgrade_legacy_20_0_ir_module_module, which is
# written by the base pre-migration before modules are renamed or merged),
# the modules on the right have to be installed, because part of its data or
# features moved there and they are neither dependencies nor auto_install.
split_modules = {
    # odoo
    # mail.tracking.value moved to mail_tracking (not auto_install)
    "mail": ["mail_tracking"],
    # the VIES check of base_vat moved to l10n_eu_account_vies
    "base_vat": ["l10n_eu_account_vies"],
    # odoo/enterprise
    # OCA/...
}

# only used here for upgrade_analysis
renamed_models = {
    # odoo
    "hr.contract.type": "hr.employee.type",
    "website.base.unit": "product.base.unit",
    # OCA/...
}

# only used here for upgrade_analysis
merged_models = {
    # odoo
    # OCA/...
}
