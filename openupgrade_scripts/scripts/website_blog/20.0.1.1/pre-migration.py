# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # `blog.post.post_date` (stored compute: `published_date` or else
    # `create_date`) is removed, 20.0 uses `published_date` only. Keep the
    # column as legacy: post-migration converts the future dates, which were
    # scheduled publications in 19.0.
    if openupgrade.column_exists(env.cr, "blog_post", "post_date"):
        openupgrade.rename_columns(env.cr, {"blog_post": [("post_date", None)]})
