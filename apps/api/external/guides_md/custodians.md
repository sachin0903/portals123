# Custodian matching

After you create the client at Seccl or SEI, record that id:

```
PUT /clients/{uid}/custodians
{ "provider": "seccl", "external_id": "their-client-id" }
```

`provider` is `seccl` or `sei`. One id per provider. Sending the same provider again replaces the id.

The check itself does not open an account. Record the custodian's client id here.
