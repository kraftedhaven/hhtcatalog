# Phase 4 Official eBay API Notes

- Traffic report: https://developer.ebay.com/api-docs/sell/analytics/resources/traffic_report/methods/getTrafficReport
  - GET /sell/analytics/v1/traffic_report
  - Required parameters include dimension, filter, and metric.
  - LISTING dimension supports up to 200 listing IDs in `listing_ids:{id|id}`.
  - Date ranges have a maximum of 90 days.
  - Metrics include CLICK_THROUGH_RATE, LISTING_IMPRESSION_SEARCH_RESULTS_PAGE, LISTING_IMPRESSION_TOTAL, LISTING_VIEWS_TOTAL, SALES_CONVERSION_RATE, and TRANSACTION.
  - Response metrics are keyed in header.metrics and values are aligned in each record.metricValues.
- Fulfillment getOrders: https://developer.ebay.com/api-docs/sell/fulfillment/resources/order/methods/getOrders
  - GET /sell/fulfillment/v1/order
  - Supports creationdate and lastmodifieddate filters, limit up to 200, and offset pagination.
  - Authorization supports sell.fulfillment and sell.fulfillment.readonly.
- OAuth scopes: https://developer.ebay.com/api-docs/static/oauth-scopes.html
  - Existing user tokens cannot gain newly requested scopes; a new authorization-code consent flow is required.
  - Current Phase 4 requested scopes are sell.analytics.readonly and sell.fulfillment.readonly in addition to existing scopes.

Implementation note: eBay does not expose a universal listing-capacity endpoint in the reviewed API sources. The Listing Cap Manager therefore reports local active count and an optional EBAY_LISTING_CAPACITY configuration value, clearly labeling the source.

No scraping is used, and all rotation decisions remain approval-only with no automatic Trading API status mutation.

## Citation URLs
[Traffic report](https://developer.ebay.com/api-docs/sell/analytics/resources/traffic_report/methods/getTrafficReport)
[Fulfillment getOrders](https://developer.ebay.com/api-docs/sell/fulfillment/resources/order/methods/getOrders)
[OAuth scopes](https://developer.ebay.com/api-docs/static/oauth-scopes.html)

