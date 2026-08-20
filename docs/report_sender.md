# Report sender — dummy flow chart

Starts at the database. The PDF is already in Google Storage. The sender categorizes the report type, fills the matching email-body template, then mails it.

```mermaid
flowchart TB
  R["1. Database row: this PDF is waiting to be sent"] --> P["2. Report sender finds that waiting row"]
  P --> D["3. Download the PDF from Google Storage"]
  D --> C["4. Categorize the PDF type. Adapt the email body to that report type"]
  C --> W["5. Database: who should get this voyage's report?"]
  W --> E["6. Email them. Body matches the type. PDF is attached"]
  E --> M["7. Mark the row sent. Do not mail it twice"]
```

Written description: [report_sender.txt](report_sender.txt)
