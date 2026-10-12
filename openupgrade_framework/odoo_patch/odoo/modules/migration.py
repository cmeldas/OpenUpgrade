# Copyright Odoo Community Association (OCA)
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).
from odoo.modules.migration import MigrationManager


def _get_files(self):
    """Turns out Odoo SA sometimes add migration scripts that interfere with
    OpenUpgrade. Those we filter out here"""
    MigrationManager._get_files._original_method(self)
    to_exclude = [("analytic", "1.2")]
    for addon, version in to_exclude:
        # scripts of the addon itself, in its migrations or upgrades folder
        for key in ("module", "module_upgrades"):
            self.migrations.get(addon, {}).get(key, {}).pop(version, None)


_get_files._original_method = MigrationManager._get_files
MigrationManager._get_files = _get_files
