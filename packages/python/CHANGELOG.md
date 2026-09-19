# Changelog

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
