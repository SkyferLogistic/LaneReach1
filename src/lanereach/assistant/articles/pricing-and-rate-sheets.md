---
slug: pricing-and-rate-sheets
title: Pricing and rate sheets
category: CRM
summary: Keep your rate Excel in SharePoint, see matching rates on every deal, check rate websites and draft priced quotes.
---
## Rate sheets

LaneReach reads your rate sheets: Excel (`.xlsx`) or CSV. Save them in the library's **Rates** folder (SharePoint/OneDrive, any sub-folder, for example `Rates/Ocean/India-Gulf.xlsx`). The library sync re-imports a sheet whenever the file changes, so the team keeps editing the same Excel as today. You can also upload a sheet on **Pricing → Rate sheets**; uploading a file with the same name replaces its rates.

Every tab is read. The header row is found by its column names, for example:

- origin: *POL, Origin, From, Port of loading*; destination: *POD, Destination, To*;
- price: *Rate, Price, All in, O/F, Buy, Sell*; currency: *Currency, Curr*;
- validity: *Valid from / Effective* and *Valid till / Validity / Expiry*;
- also *Equipment / Container*, *Carrier / Line / Airline*, *Transit / TT*, *Unit / Per*, *Charge / Surcharge*, *Min*, *Remarks*.

A column per container size (*20GP, 40GP, 40HC, 45HC*) or per air weight break (*-45, +45, +100, +500*) becomes one rate per column. Blank lane cells repeat the row above (merged cells). Amounts may include currency symbols, thousands separators or decimal commas.

If a guess is wrong (for example LaneReach used *Buy* but you quote from *Sell*), open the sheet and pick the right column under **Columns**, then save: the stored file is re-imported. Rows that cannot be read are listed with their tab and row number; the rest of the sheet still imports. A file that cannot be opened at all keeps the previous rates.

## Rates on a deal

Open a deal → **Pricing**. LaneReach shows the rate options that match the deal:

- the same lane: port/airport codes (UN/LOCODE or IATA, also in brackets such as *Jebel Ali (AEJEA)*), else the same place name;
- a compatible mode and equipment;
- valid on the cargo-ready date.

Each option is freight plus the surcharges from the same tab, carrier and validity, with the all-in total for the shipment (containers × count, air on chargeable weight, LCL on weight/measure) and why it matched. Charges in another currency than the freight are shown but not added: LaneReach does not convert currencies.

When the deal spells the place differently from your sheet, **Match the lane with AI** asks the AI to pick the same place from the places in your sheets. It can only choose places that exist there, and prices always come from the sheet.

## Draft a quote from rates

**Draft quote from these rates** creates a draft quote with one line per charge, in the freight currency, valid no longer than the rates behind it. Add a markup % when the sheet holds buy rates. Where the prices came from (sheet, tab, row) is written in the deal's timeline, not on the customer's quote. In the quote builder, **Fill from rate sheets** replaces a draft's lines the same way. A draft is never sent automatically.

The workflow step **Create quote draft** prices from the sheets too when a sheet covers the lane (setting *Price from rate sheets*, on by default), otherwise it adds the usual charges with empty prices.

## Rate websites and market checks

Add the websites your team checks under **Pricing → Websites**: a name, the address, and optionally a lane link such as `https://site.example/search?from={origin}&to={destination}&type={equipment}`. Search on the site once, copy the address and replace the places with `{origin}` and `{destination}` (also `{mode}`, `{equipment}`, `{date}`). Links must use https and cannot contain a user name or password.

On a deal, **Open on …** opens the site with the lane filled in. **Record rate** saves the rate you saw (amount, currency, per unit, validity, note) on the deal. Recorded rates are listed next to the sheet rates, with the cheapest highlighted, and they show up in **Pricing → Lane history** together with the sheet rates for the lane.

LaneReach never logs into rate websites, stores their passwords or copies prices from their pages. Official connections can be added for sites that offer an API.
