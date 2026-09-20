"""Environment-specific settings.

Pick one with DJANGO_SETTINGS_MODULE:

    config.settings.dev     local development
    config.settings.prod    deployed
    config.settings.test    the test suite

``manage.py`` defaults to ``config.settings.dev``; ``pytest.ini`` selects
``config.settings.test``.  There is no importable ``config.settings`` module
that guesses - being explicit is what stops a production box booting with
DEBUG on.
"""
