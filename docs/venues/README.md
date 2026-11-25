# Venue profile

每一家交易所一份。C6-03、C7-03～C7-05 的程式，以及 C8 的每一家，都照這裡寫。程式與 profile 不一致時，先改 profile，再改程式。

第一份是 [binance-um.md](binance-um.md)。檔名用 venue 的 kebab-case（`BinanceUM` → `binance-um.md`）。

## 怎麼寫

- 文字用中文。端點、欄位、錯誤碼、狀態維持文件上的原文。
- 每一條附官方文件的連結與讀取日期（`YYYY-MM-DD`）。
- weight、筆數上限、時間窗、回查期限只從文件抄。頁面沒寫，就寫「未確認」，並附上查過的頁面。不估計、不沿用另一家或另一份文件的數字。
- changelog 與端點頁不一致時，以讀取日的端點頁為準，兩處都附連結。
- Binance 的 testnet host 寫在 [binance-um.md](binance-um.md)（C7-09）。Bybit testnet、OKX demo 仍不寫，等 C8-03、C8-04。
- 不在 profile 裡加 client、job 或 migration。

## 章節

每份都有這九節，順序固定。

1. **適用範圍。** kind、API 家族、正式站 host。簽章與既有的驗證呼叫若已存在，寫路徑，避免和快照端點搞混。
2. **快照。** 各錢包的餘額與持倉端點。欄位對到 C6-02 的 `ledger.balance_snapshot`、`ledger.position_snapshot`：數量、方向、開倉價、mark price、未實現損益、保證金。單向與雙向持倉怎麼表示。空帳號也要能寫出 batch（ARCHITECTURE §4.6）。
3. **歷史。** 成交、訂單、funding 的端點。分頁（時間窗、`fromId`、`orderId`）、單次筆數上限、時間窗上限，以及哪些參數不能同時送。
4. **查詢期限。** 訂單、成交、income 各能回查多久。過期之後是空結果還是錯誤碼；文件沒寫就標未確認。
5. **限速。** 以 IP 還是帳號計、各端點的 weight、回報用量的 header、超限的回應（429、418）與 `Retry-After`。文件沒有的 header 標未確認。
6. **錯誤碼。** 對到 C3-04 的 `auth`、`rate_limited`、`ip_banned`、`unavailable`、`bad_request`、`not_found`。與該家 client 的對照函式並列。不一致時寫目標類別、現況、要改的檔案與票。`auth` 對 credential 的影響寫上決策編號。
7. **訂單終態。** 哪些狀態算已結束。定義在 ARCHITECTURE §4.5：`FILLED`，以及有成交的取消／過期；部分成交不算。該家文件的狀態枚舉整份列出，對得上與對不上的分開寫。
8. **去重鍵。** 成交、訂單、funding 在這一家的唯一鍵，用文件裡的欄位。文件沒寫跨 symbol 是否唯一時，鍵要帶上文件已證明的範圍（通常是 symbol），並把沒寫的部分標未確認。
9. **單位與商品。** qty 與價格以什麼計。商品對到 SYM UID 的反查是 `(venue, category, exch_ticker)`（C4-02）。端點沒有的那段（多半是 category）寫明從 `mirror.symbol` 來，不在 profile 裡編。

文末加一節「與現行程式不一致」，列出要改的一邊與票號。沒有不一致也寫一行。
