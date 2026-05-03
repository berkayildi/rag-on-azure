# Changelog

## [0.1.0](https://github.com/berkayildi/rag-on-azure/compare/v0.0.1...v0.1.0) (2026-05-03)


### Features

* add bicep modules for monitor, kv, search, openai, container app ([5e57808](https://github.com/berkayildi/rag-on-azure/commit/5e578084f09193d88bc88fe872c0be8f7a31ed4d))
* **infra:** optional developer principal rbac on search service ([2db97f2](https://github.com/berkayildi/rag-on-azure/commit/2db97f2eb2aabd8328d4eff0aebff4548b8abbb9))
* **infra:** optional developer rbac on openai account ([908e2ea](https://github.com/berkayildi/rag-on-azure/commit/908e2eabbc4e4e3faff59841a52f804e026432a1))
* **ingest:** emit .fetched.jsonl index from fetch run ([5616f42](https://github.com/berkayildi/rag-on-azure/commit/5616f42358d72479b20bf64e18e239c2c3930241))
* **ingest:** implement chunk into chunks.jsonl ([9088c73](https://github.com/berkayildi/rag-on-azure/commit/9088c73c44d96fb970b83f13806c32238dc52f02))
* **ingest:** implement fetch with hash-based cache ([a16dcdb](https://github.com/berkayildi/rag-on-azure/commit/a16dcdb63139d4cb5fb657aaad9c30573b07961d))
* **ingest:** index schema with hnsw vector profile ([79f433f](https://github.com/berkayildi/rag-on-azure/commit/79f433fb5db7547faac43ca798aea3089fde51db))
* **ingest:** orchestrator with content_hash idempotence ([9e0ab90](https://github.com/berkayildi/rag-on-azure/commit/9e0ab906c0d4fb69a439547b226d2967b3af1001))
* **ingest:** provisional azure openai embedding client ([0031cd1](https://github.com/berkayildi/rag-on-azure/commit/0031cd151310c314fb5a518fb0085d3850b4471b))


### Bug Fixes

* **infra:** chat deployment to gpt-4o, sweden central standard ([27e1c64](https://github.com/berkayildi/rag-on-azure/commit/27e1c644c810b38b9df401e532f4ca883a2c3dcd))
* **infra:** chat model version to 2024-11-20 (validator rejects 2024-08-06) ([87ea265](https://github.com/berkayildi/rag-on-azure/commit/87ea265fa5bc1cacf6a776e5c11f08f3afcea57d))
* **infra:** default to swedencentral, mixed openai skus (standard + datazonestandard) ([0fc1803](https://github.com/berkayildi/rag-on-azure/commit/0fc1803062ad94d68cbaab9dee219db0d06adbd8))
* **ingest:** add aiohttp dep for azure-identity.aio transport ([0932f26](https://github.com/berkayildi/rag-on-azure/commit/0932f2613b82de42369d1597b4ec8082ad9e8b00))
* **ingest:** partial fetch failure should not abort pipeline ([459251a](https://github.com/berkayildi/rag-on-azure/commit/459251ae06d6c29315bc51cbdcbc047ff8ace5a4))
* **ingest:** update broken hmrc-aml-tcsp url to current gov.uk path ([c224777](https://github.com/berkayildi/rag-on-azure/commit/c224777f9143247387e29aff8ec4c995afd19538))


### Documentation

* align rg naming convention with azd default ([80eb166](https://github.com/berkayildi/rag-on-azure/commit/80eb16676d76e43e23532525898d5884e846aa74))
* fix azd down flag (--force-delete is not valid) ([6187364](https://github.com/berkayildi/rag-on-azure/commit/61873641e6effb6bf7f8a672a00f9e333ae4cdc9))
* note pre-commit mypy deps stay in sync with pyproject.toml ([e0ad6e5](https://github.com/berkayildi/rag-on-azure/commit/e0ad6e5722176bd67500245f82cb2d136a74e23e))
* route readme + agents.md through make targets ([5d03201](https://github.com/berkayildi/rag-on-azure/commit/5d0320157a37093a1339d30ac10facf392b4e80f))
* switch chat model to gpt-4o due to gpt-4o-mini deprecation ([05103bd](https://github.com/berkayildi/rag-on-azure/commit/05103bd4d8fa105a64af9e592867e0cd837fbd6d))
* switch default region to sweden central with mixed openai skus ([992cb22](https://github.com/berkayildi/rag-on-azure/commit/992cb22dd841ebb1e9c877fee207fd62a6f8cdd4))
* update skill location ([410dff3](https://github.com/berkayildi/rag-on-azure/commit/410dff3dd26ff94a5cf40d026182c388f95dbd75))
