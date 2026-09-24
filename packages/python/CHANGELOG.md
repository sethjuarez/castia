# Changelog

## [0.10.0](https://github.com/sethjuarez/castia/compare/python-v0.9.0...python-v0.10.0) (2026-09-24)


### ⚠ BREAKING CHANGES

* **python:** /responses now returns 400 unsupported_parameter for non-null previous_response_id and conversation instead of accepting and ignoring them. Send full prior turns in input, omit these fields, or send null when using Castia's lightweight process-local Responses runtime.

### Features

* **python:** add local trace inspection command ([889fdec](https://github.com/sethjuarez/castia/commit/889fdecdc7eed3fa9d946ce60476805136b26afe))
* **python:** add local trace sink core ([c642457](https://github.com/sethjuarez/castia/commit/c642457839659a594d5f58ac7c0a6db7308152ec))
* **python:** bridge Prompty traces to local sinks ([3ae2912](https://github.com/sethjuarez/castia/commit/3ae29129182c796eeb48d3f3d16ea074a4e9a590))
* **python:** emit local runtime trace events ([0b5fb5d](https://github.com/sethjuarez/castia/commit/0b5fb5dc795ca5b0030174829f30a22b7325c91b))
* **python:** export local traces to otel ([bb574d9](https://github.com/sethjuarez/castia/commit/bb574d92d5342392c01e746b360ea631a24efe07))
* **python:** reject unsupported responses history parameters ([594b74a](https://github.com/sethjuarez/castia/commit/594b74a2ab74dd4881e4fd6f3c7b6e348a9c2c2f))


### Bug Fixes

* **python:** accept hosted responses conversation context ([24b4387](https://github.com/sethjuarez/castia/commit/24b4387fda3d0bfa76d08bbe6b7259cbdb955134))
* **python:** add completed responses lifecycle routes ([42f34f0](https://github.com/sethjuarez/castia/commit/42f34f09a94fe0d55033ceda82e0eefc4d599656))
* **python:** align activity response headers ([446814e](https://github.com/sethjuarez/castia/commit/446814e65f4acb4c50d8b2bbf09ed971b8eb8251))
* **python:** align responses SSE event metadata ([b6f1794](https://github.com/sethjuarez/castia/commit/b6f1794e6724cb17f0ba53c422754b6be81af651))
* **python:** clarify responses cancel errors ([db5962e](https://github.com/sethjuarez/castia/commit/db5962ecfbb837e7b0cbd1eee6169bb5665ffb8c))
* **python:** emit failed responses for stream errors ([c79e213](https://github.com/sethjuarez/castia/commit/c79e213c6482057b1d971de95387379bf4747627))
* **python:** guard responses request field echoes ([ce27bb2](https://github.com/sethjuarez/castia/commit/ce27bb23de994123809ea7fd6dcf7ee265e24838))
* **python:** make immediate responses sdk parseable ([c41cdf2](https://github.com/sethjuarez/castia/commit/c41cdf2e3e5d4d7d1e1f5b7c9247825aa0d7906a))
* **python:** preserve activity headers on failures ([37fa317](https://github.com/sethjuarez/castia/commit/37fa3176e9fa5703532bd3bc829254c3cb067453))
* **python:** propagate Foundry request context ([d135276](https://github.com/sethjuarez/castia/commit/d135276818c1cb2e3eb449c0cb848c0c0d044eb6))
* **python:** reject malformed wire protocol bodies ([564456a](https://github.com/sethjuarez/castia/commit/564456ae2a854cca487f4cbe81695ea9255d19d1))
* **python:** strengthen responses object shape ([057f3c9](https://github.com/sethjuarez/castia/commit/057f3c9a685b3e333b88fcbc23fdaea028ba11d7))


### Documentation

* **python:** clarify responses lifecycle limits ([19504b7](https://github.com/sethjuarez/castia/commit/19504b76c1024e1e543258ce5832a548ba705a32))

## [0.9.0](https://github.com/sethjuarez/castia/compare/python-v0.8.0...python-v0.9.0) (2026-09-20)


### Features

* **python:** add local dev command ([55d6a05](https://github.com/sethjuarez/castia/commit/55d6a0559157a731ee51c98a6c25c8d8d3eae691))
* **python:** add local dev diagnostics ([c60e0a7](https://github.com/sethjuarez/castia/commit/c60e0a7040dd5ba0bd6af0eee1401add25c8291b))
* **python:** derive Prompty toolbox schemas from MCP ([f528e27](https://github.com/sethjuarez/castia/commit/f528e27dff3026995b3864c6af5303df5270d399))


### Bug Fixes

* **python:** compact Prompty MCP references ([564700a](https://github.com/sethjuarez/castia/commit/564700ae30bbd5452c0dced4696d1517fb8c2585))

## [0.8.0](https://github.com/sethjuarez/castia/compare/python-v0.7.8...python-v0.8.0) (2026-09-19)


### Features

* **python:** add optional Prompty runtime harness ([#52](https://github.com/sethjuarez/castia/issues/52)) ([6c903b6](https://github.com/sethjuarez/castia/commit/6c903b6cda634e9c7c049761824e17507484cd1f))

## [0.7.8](https://github.com/sethjuarez/castia/compare/python-v0.7.7...python-v0.7.8) (2026-09-19)


### Bug Fixes

* **python:** record structured model input traces ([#50](https://github.com/sethjuarez/castia/issues/50)) ([e681298](https://github.com/sethjuarez/castia/commit/e681298918869bbcf7ce03b555d20ce9c468f4f6))

## [0.7.7](https://github.com/sethjuarez/castia/compare/python-v0.7.6...python-v0.7.7) (2026-09-19)


### Bug Fixes

* **python:** suppress noisy ASGI send spans ([#47](https://github.com/sethjuarez/castia/issues/47)) ([c658f15](https://github.com/sethjuarez/castia/commit/c658f159e9612ce50bbf8ce4e546ae6e69fb6405))

## [0.7.6](https://github.com/sethjuarez/castia/compare/python-v0.7.5...python-v0.7.6) (2026-09-19)


### Bug Fixes

* stabilize playground and pyproject deploy guidance ([#45](https://github.com/sethjuarez/castia/issues/45)) ([727bbcd](https://github.com/sethjuarez/castia/commit/727bbcd851342b51d87363a47169914b00faadc3))

## [0.7.5](https://github.com/sethjuarez/castia/compare/python-v0.7.4...python-v0.7.5) (2026-09-19)


### Bug Fixes

* **python:** restrict readiness diagnostics to local requests ([81b0508](https://github.com/sethjuarez/castia/commit/81b0508f3ba5037c67ad75513637ec5415272ba1))

## [0.7.4](https://github.com/sethjuarez/castia/compare/python-v0.7.3...python-v0.7.4) (2026-09-19)


### Bug Fixes

* harden local agent playground startup ([88b3b6d](https://github.com/sethjuarez/castia/commit/88b3b6d030293e4f298be105e11be1ac55ddb6d8))

## [0.7.3](https://github.com/sethjuarez/castia/compare/python-v0.7.2...python-v0.7.3) (2026-09-19)


### Bug Fixes

* **python:** use editable requirements shim for scaffold ([157c524](https://github.com/sethjuarez/castia/commit/157c5240025d465804ce16c35fa15d27b1809193))

## [0.7.2](https://github.com/sethjuarez/castia/compare/python-v0.7.1...python-v0.7.2) (2026-09-18)


### Bug Fixes

* **python:** stream responses fallback for portal ([fb00673](https://github.com/sethjuarez/castia/commit/fb00673c3117e7b822f8cf04af7792dae8adb523))

## [0.7.1](https://github.com/sethjuarez/castia/compare/python-v0.7.0...python-v0.7.1) (2026-09-18)


### Bug Fixes

* **python:** catch hosted deploy diagnostics earlier ([be71c57](https://github.com/sethjuarez/castia/commit/be71c57dcd2af432a6b725e6b0531dc10ea108b3))

## [0.7.0](https://github.com/sethjuarez/castia/compare/python-v0.6.0...python-v0.7.0) (2026-09-18)


### Features

* add agent playground story flow baseline ([fdfdccd](https://github.com/sethjuarez/castia/commit/fdfdccd97fd45a15dd78ae35727fb83476dc1182))
* **python:** add SFT and DPO fine-tuning ([#25](https://github.com/sethjuarez/castia/issues/25)) ([4a2daea](https://github.com/sethjuarez/castia/commit/4a2daead31517037304e0c5df48472f3d71ce227))
* **python:** refine Castia starter and playground flow ([65abf95](https://github.com/sethjuarez/castia/commit/65abf95d947590091fe80ff24b1df510071d8489))
* **rust:** add typra-governed runtime slices ([68bd555](https://github.com/sethjuarez/castia/commit/68bd555ec77b1b7d191ce6799328d4a6c342740f))


### Bug Fixes

* package starter kit for Copilot plugin install ([9d9d0d3](https://github.com/sethjuarez/castia/commit/9d9d0d3e6a7dee73fff120bd0d89e63aab57e754))

## [0.6.0](https://github.com/sethjuarez/castia/compare/python-v0.5.0...python-v0.6.0) (2026-09-16)


### ⚠ BREAKING CHANGES

* **python:** Legacy flat Python submodule imports were removed. Use the root public API or documented capability paths.

### Features

* **python:** complete agent lifecycle and organize SDK capabilities ([8ab56e4](https://github.com/sethjuarez/castia/commit/8ab56e454c6f6ca982616f3d377227b418f33308))

## [0.5.0](https://github.com/sethjuarez/castia/compare/python-v0.4.0...python-v0.5.0) (2026-09-10)


### Features

* **python:** optimize toolbox tool descriptions ([34b28c0](https://github.com/sethjuarez/castia/commit/34b28c0dd39a53e120ab13c635e28fb2e657f224))

## [0.4.0](https://github.com/sethjuarez/castia/compare/python-v0.3.0...python-v0.4.0) (2026-09-09)


### Features

* **python:** consume a Foundry toolbox as a server-side mcp tool ([#12](https://github.com/sethjuarez/castia/issues/12)) ([fb476ee](https://github.com/sethjuarez/castia/commit/fb476eee4a915238bc8625407845a0229266020f))


### Documentation

* **python:** fix README quickstart to use [@app](https://github.com/app).activity ([#10](https://github.com/sethjuarez/castia/issues/10)) ([d542d8d](https://github.com/sethjuarez/castia/commit/d542d8d5c67f3e5bcc57ebe7beb86065ac22be04))

## [0.3.0](https://github.com/sethjuarez/castia/compare/python-v0.2.0...python-v0.3.0) (2026-09-09)


### Features

* **python:** add reinforcement fine-tuning (RFT) tooling ([#7](https://github.com/sethjuarez/castia/issues/7)) ([971646c](https://github.com/sethjuarez/castia/commit/971646cb15a2480d7026cf4ef0dc419a0eddbb23))

## [0.2.0](https://github.com/sethjuarez/castia/compare/python-v0.1.0...python-v0.2.0) (2026-09-09)


### Features

* **python:** add Foundry Agent Optimizer support ([#5](https://github.com/sethjuarez/castia/issues/5)) ([2d3103a](https://github.com/sethjuarez/castia/commit/2d3103a58f7165f9c6f18deac49f948ab05daa83))

## 0.1.0 (2026-09-09)


### Continuous Integration

* automate per-language releases with release-please + PyPI Trusted Publishing ([#3](https://github.com/sethjuarez/castia/issues/3)) ([cb9db23](https://github.com/sethjuarez/castia/commit/cb9db23c1dda6ad270d51339f5adde829925c045))
