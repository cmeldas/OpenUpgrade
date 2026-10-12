# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)


def _is_valid_path(env, model_name, path):
    """Whether the '/'-separated field path saved by the import dialog still
    resolves on model_name."""
    if model_name not in env:
        return False
    model = env[model_name]
    for name in path.split("/"):
        if name in ("id", ".id"):
            return True
        field = model._fields.get(name)
        if field is None:
            return False
        if not field.relational:
            return True
        model = env[field.comodel_name]
    return True


def _drop_stale_import_mappings(env):
    """base_import.mapping remembers which column of an imported file was
    mapped to which field path, and the import dialog suggests it again as is.
    Fields and models renamed or removed by the migration (e.g.
    mrp.production.product_uom_id -> uom_id) are not renamed in this table by
    openupgradelib.rename_fields/rename_models, so the dialog would propose a
    path that no longer exists. Run in end-migration, once every module has
    been updated, and drop only such stale rows: the next import learns the
    mapping again."""
    env.cr.execute(
        """
        SELECT id, res_model, field_name
        FROM base_import_mapping
        WHERE COALESCE(field_name, '') != ''
        """
    )
    stale_ids = []
    for mapping_id, res_model, field_name in env.cr.fetchall():
        if not _is_valid_path(env, res_model, field_name):
            _logger.warning(
                "base_import.mapping %s: %s.%s no longer exists, mapping dropped",
                mapping_id,
                res_model,
                field_name,
            )
            stale_ids.append(mapping_id)
    if stale_ids:
        openupgrade.logged_query(
            env.cr,
            "DELETE FROM base_import_mapping WHERE id IN %s",
            (tuple(stale_ids),),
        )


@openupgrade.migrate()
def migrate(env, version):
    _drop_stale_import_mappings(env)
