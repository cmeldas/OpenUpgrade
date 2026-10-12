# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import Command

from openupgradelib import openupgrade


def blog_post_scheduled_publication(env):
    """In 19.0 a published post whose `published_date` was in the future
    stayed hidden until that date. 20.0 publishes such posts with the
    `publish_on` scheduler instead and stamps `published_date` itself when
    the post goes online.
    Drafts keep an empty `published_date` (their 19.0 `post_date` was only
    the creation date), so 20.0 stamps the real date when they get published.
    """
    cr = env.cr
    scheduled = openupgrade.logged_query(
        cr,
        """
        UPDATE blog_post
        SET publish_on = published_date,
            is_published = FALSE,
            published_date = NULL
        WHERE is_published
            AND published_date > (now() AT TIME ZONE 'UTC')
            AND publish_on IS NULL
        """,
    )
    if scheduled:
        env["blog.post"].invalidate_model(
            ["publish_on", "is_published", "published_date"]
        )
        env["blog.post"]._manage_next_scheduled_action()


def blog_manager_group(env):
    """20.0 restricts the edition of blogs, posts and tags to the new group
    `website_blog.group_website_blog_manager` (19.0: website designers).
    Keep the rights of the current internal website designers.
    """
    blog_manager = env.ref(
        "website_blog.group_website_blog_manager", raise_if_not_found=False
    )
    designer = env.ref("website.group_website_designer", raise_if_not_found=False)
    if not blog_manager or not designer:
        return
    users = designer.with_context(active_test=False).all_user_ids.filtered(
        lambda user: not user.share
    )
    users -= blog_manager.with_context(active_test=False).all_user_ids
    if users:
        blog_manager.write({"user_ids": [Command.link(user.id) for user in users]})


def mail_template_technical_usage(env):
    """The noupdate mail body template gets the new technical usage."""
    view = env.ref("website_blog.blog_post_template_new_post", raise_if_not_found=False)
    if view and "technical_usage" in view._fields and not view.technical_usage:
        view.technical_usage = "mail_post_source"


@openupgrade.migrate()
def migrate(env, version):
    blog_post_scheduled_publication(env)
    blog_manager_group(env)
    mail_template_technical_usage(env)
