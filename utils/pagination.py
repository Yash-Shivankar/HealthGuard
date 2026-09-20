"""Shared DRF pagination."""

from rest_framework.pagination import PageNumberPagination


class DefaultPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    # Capped: an uncapped page_size on a patient list is a bulk-extraction
    # endpoint (AUDIT 8.8).
    max_page_size = 100
