Upload a selfie and a passport, driving licence, or identity document, then run an identity check on those files. AML uses the client already stored. Neither call takes a URL, and a check does not open an account.

`POST /clients/{uid}/documents` takes JSON. `file` is raw base64, with no `data:` prefix. A passport, driving licence, or identity document needs `issuing_country`. A selfie does not. A decoded file over 50 MB is `422` `file_too_large`.

`side` is `front` (the default) or `back`. Upload the back of a driving licence or identity document as its own document, with the same `type` and `issuing_country` and `side: "back"`. A passport and a selfie have a front only. The response is `uid`, `type`, `side`, and `issuing_country`.

`POST /clients/{uid}/checks` with `{ "kind": "identity" }` needs a selfie and the front of one identity document. A back on its own is not enough. If they are missing the code is `documents_required`. The check uses the newest selfie and the newest front, with the newest back of that document when there is one. `document_uids` lists the documents held when the check started. An issuing country outside the person's nationality comes back `refer`.

`{ "kind": "aml" }` screens the stored name, date of birth, and address country. No address country is `422` `address_country_required`.

`GET /clients/{uid}/checks` returns the latest `identity` and `aml` status, then every check, newest first. Statuses are `pending`, `clear`, `refer`, and `rejected`. `GET /checks/{uid}` returns one check. A webhook carries later updates; that GET stays the source of truth.
