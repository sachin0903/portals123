`POST /clients` creates a client. `type` defaults to `person`.

A person needs `first_name`, `last_name`, `dob`, `email`, at least one `nationality`, and `address`. `address.country` is required.

A company needs `email`, `company.name`, and a country: `company.incorporation_country` or `address.country`. A company does not need a nationality.

`identities` is optional. Each item has `type` (`national_insurance_number`, `ssn`, `social_insurance_number`, `tax_identification_number`, or `national_identity_number`) and `identifier`. A type may appear once.

`GET /clients` lists this firm's clients. `GET /clients/{uid}` reads one. `PATCH /clients/{uid}` updates only the fields you send.
