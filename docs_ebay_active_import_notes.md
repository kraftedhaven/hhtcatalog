# eBay Active Listing Import Notes

## Official sources consulted

- [GetMyeBaySelling reference](https://developer.ebay.com/devzone/xml/docs/reference/ebay/getmyebayselling.html)
- [Trading API request input data and OutputSelector guidance](https://developer.ebay.com/api-docs/user-guides/static/make-a-call/tapi-input-data.html)
- [Trading API field index](https://developer.ebay.com/devzone/xml/docs/reference/ebay/fieldindex.html)

## Confirmed behaviors

The `GetMyeBaySelling` call returns active seller listings when `ActiveList.Include` is true. Its response uses `ActiveList.ItemArray.Item` entries and supports pagination. The official reference says `DetailLevel=ReturnAll` requests all selling containers, although field availability can remain constrained by the response behavior.

The Trading API supports the optional, repeatable `OutputSelector` filter. When selectors are present, only requested fields plus parents and children are returned. The request-data guide says a full path is required if a field can occur in multiple response containers, and that the call request type should not be included in an OutputSelector path. It gives leaf-name examples for `GetItem`, while the Trading API field index names active-selling containers as `GetMyeBaySelling.ActiveList.ItemArray.Item.<field>`.

`PrimaryCategory` is an Item container with nested `CategoryID` and `CategoryName`; the active importer parser must read this nested object when it is present. A direct `GetItem` detail request returns this shape and can be used as a bounded, read-only fallback to fill category and item-specific data for selected active listing IDs.

## Live diagnostic outcome

After an active-list refresh on 2026-09-21, eBay returned 504 active records but the minimal response did not include PrimaryCategory for most records. An explicit collection of `ActiveList.ItemArray.Item.*` OutputSelectors was rejected by eBay before import and was removed immediately. No eBay listing changes were made by these requests. The stable request retains `DetailLevel=ReturnAll` without OutputSelector filters. The existing 1–20 record read-only `GetItem` enrichment job is the authoritative source for category and required-specific extraction until a valid bulk Trading API selector form is confirmed.
