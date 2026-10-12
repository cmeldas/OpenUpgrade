This module is a technical module that contains a number of
monkeypatches to improve the behaviour of Odoo when migrating your
database using the OpenUpgrade migration scripts:

- Prevent dropping columns or tables in the database when fields or
  models are obsoleted in the Odoo data model of the target release.
  After the migration, you can review and delete unused database tables
  and columns using database_cleanup. See
  <https://odoo-community.org/shop/product/database-cleanup-918>
- When data records are deleted during the migration (such as views or
  other system records), this is done in a secure mode. If the deletion
  fails because of some unforeseen dependency, the deletion will be
  cancelled and a message is logged, after which the migration
  continues. Checks that prevent the deletion of records
  (`@api.ondelete(at_uninstall=False)`) don't prevent the deletion of
  obsolete records, and the data linked to removed models (attachments
  and their files, messages, followers) is kept.
- When the database refuses to convert a column to the new type of its
  field, the data is kept in a legacy column (see
  `openupgradelib.openupgrade.get_legacy_name`), an empty column of the
  new type is created and an error is logged, instead of aborting the
  migration.
- Prevent a number of log messages that do not apply when using
  OpenUpgrade.
- Suppress log messages about failed view validation, which are to be
  expected during a migration.
- Run migration scripts for modules that are installed as new
  dependencies of upgraded modules (when there are such scripts for
  those particular modules)
- Install new auto_install modules whose dependencies are installed
  (except localization modules, modules for countries none of the
  companies is in, and modules listed in `no_auto_install_modules` of
  openupgrade_scripts' `apriori.py`), as well as the modules listed in
  `split_modules` of `apriori.py` (a mapping of modules of the previous
  version to the new modules that took over part of their models, data or
  features, for instance `{"mail": ["mail_tracking"]}`) when the module
  of the previous version was installed before the migration.
- Production databases generated with demo data, will be transformed to
  non-demo ones. If you want to avoid that, you have to pass through the
  environment variable OPENUPGRADE_USE_DEMO, the value "yes".

For detailed documentation see:

- <https://github.com/OCA/OpenUpgrade/>
- <https://oca.github.io/OpenUpgrade>
