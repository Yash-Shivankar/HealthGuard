"""App configuration for the project package.

Replacing ``django.contrib.admin`` with this in INSTALLED_APPS makes
``admin.site`` the HealthGuard site, so the ordinary ``@admin.register``
decorator registers against it and there is no second registry to keep in
step.
"""

from django.contrib.admin.apps import AdminConfig


class HealthGuardAdminConfig(AdminConfig):
    default_site = "config.admin.HealthGuardAdminSite"
