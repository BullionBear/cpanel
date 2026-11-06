# cpanel

cpanel 是 [mftiko](https://github.com/BullionBear/mftiko) 的外部 PMS：管理交易所帳號與唯讀 key，持續落庫訂單、成交、funding 歷史，並定時取餘額與持倉快照。

目前正在以 Go 重建（v3）。v1、v2 已封存，見 [`archive/`](archive/README.md)。

## 文件

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：架構、與 mftiko 的邊界、不變式
- [docs/PLAN.md](docs/PLAN.md)：一期的 epic 與 ticket
- [docs/DECISIONS.md](docs/DECISIONS.md)：決策紀錄

## 進度

一期 8 個 epic：#32（C1 封存 v2）到 #39（C8 其他 venue）。v3 的程式碼從 C2（#33）開始放進這個 repo。

## 與 mftiko 的關係

cpanel 只使用四樣輸入（mftiko E21-D085）：mftiko API（只發 GET）、SYM（gRPC）、cpanel 自己的唯讀交易所 key、交易所 REST。不連 mftiko 的 etcd、資料庫或 `td.evt`。

## 目錄

```
archive/   v1、v2 的程式碼，只供閱讀
docs/      v3 的文件
```
