# BinanceUM

讀取日 2026-11-23。這一頁的數字都來自當天打開的端點頁。testnet 的 host 不在這份，由 C7-09 補。

正式站 host：`https://fapi.binance.com`（[General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)）。

## 1. 適用範圍

| 項目 | 值 |
|---|---|
| kind | `BinanceUM` |
| exchange | `binance` |
| venue | `BinanceUM` |
| API 家族 | `fapi`（`pms.account_kind` 的種子） |
| 正式站 | `https://fapi.binance.com` |

呼叫是簽過名的 USER_DATA GET：header `X-MBX-APIKEY`，query 附 `timestamp` 與 `signature`。`recvWindow` 不送時文件預設 5000；現行程式送 `5000`。多數端點的上限是 60000。

`credential.verify` 對這個 kind 打的是既有的 `GET /fapi/v2/account`（`internal/venue/binance` 的 `ReadFamily`）。下面的快照不沿用這支。

## 2. 快照

一個錢包：USD-M 合約錢包。逐倉保證金在持倉列上，不另打一支餘額端點。現貨、CM、PM 不在這份。

每次快照兩支 GET，都不帶 `symbol`，把帳號上的列一次拿完。

| 用途 | 方法 | weight |
|---|---|---|
| 餘額 | `GET /fapi/v3/balance` | 5 |
| 持倉 | `GET /fapi/v3/positionRisk` | 5 |

餘額頁：[Futures Account Balance V3](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account#futures-account-balance-v3)。持倉頁：[Position Information V3](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade#position-information-v3)。

`GET /fapi/v3/account`（weight 5）同時有 `assets` 與 `positions`，但那份 `positions` 沒有 `entryPrice`、`markPrice`。開倉價與 mark 只在 `positionRisk`。快照不打 account v3。

### 餘額 → `ledger.balance_snapshot`

v3 與 v2 的餘額欄位相同。C6-02 的表還沒有，先對到這些欄位：

| 概念 | 欄位 |
|---|---|
| 資產 | `asset` |
| 錢包餘額 | `balance` |
| 可用 | `availableBalance` |
| 全倉錢包餘額 | `crossWalletBalance` |
| 可轉出 | `maxWithdrawAmount` |

`locked` 這兩頁都沒有。未確認。

### 持倉 → `ledger.position_snapshot`

v3 只回「有持倉或有掛單」的 symbol。欄位說明寫 `positionAmt` 是「positive for long, negative for short」。

| 概念 | 欄位 |
|---|---|
| 數量 | `positionAmt`（原樣，不取絕對值） |
| 方向 | `positionSide` |
| 開倉價 | `entryPrice` |
| mark price | `markPrice` |
| 未實現損益 | `unRealizedProfit`（v3 的大小寫；account v3 的 `positions` 寫成 `unrealizedProfit`） |
| 保證金 | `isolatedMargin`。同列還有 `initialMargin`、`positionInitialMargin`、`isolatedWallet`。C6-02 從這幾個裡定欄位，這份保持原欄位 |

`positionAmt` 為 0 的列是有掛單、沒有持倉，不寫 `position_snapshot`。整份是空的也要寫 `snapshot_batch`（ARCHITECTURE §4.6），否則「最新」會讀到上一筆非空的快照。

### 單向與雙向

`positionSide` 的枚舉是 `BOTH`、`LONG`、`SHORT`（[Public Endpoints Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/common-definition)）。

- `BOTH`：單向。方向再看 `positionAmt` 的正負。
- `LONG`／`SHORT`：雙向。方向用 `positionSide`。文件沒有寫雙向的空單一定是正數，所以不把 `SHORT` 的 `positionAmt` 取絕對值。

`GET /fapi/v1/positionSide/dual`（weight 30）回 `dualSidePosition`：`true` 是雙向，`false` 是單向（[Get Current Position Mode](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account#get-current-position-mode)）。每一列已有 `positionSide`，快照不打這支。

`marginType` 只在 `GET /fapi/v2/positionRisk`（weight 也是 5）。v3 沒有這個欄位，也沒有 `leverage`。這份快照不打 v2。全倉或逐倉未確認要不要另存；保證金金額用上面的 v3 欄位。

## 3. 歷史

三條都是 USER_DATA GET。成交與訂單以 symbol 為單位。income 是帳號層級，`symbol` 可省略。

### 成交 `GET /fapi/v1/userTrades`

[Account Trade List](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade#account-trade-list)。weight 5。`symbol` 必填。

| 參數 | 文件 |
|---|---|
| `limit` | 預設 500，最大 1000 |
| `startTime`、`endTime` | 兩個都不送時，"the last 7 days' data will be returned"。間隔 "cannot be longer than 7 days" |
| `fromId` | 「Trade id to fetch from. Default gets most recent trades.」不能與 `startTime` 或 `endTime` 同送 |
| `orderId` | 要與 `symbol` 一起送。這是篩選，不是翻頁 cursor |

回補沿 ARCHITECTURE §4.5：從 `since − 1 分鐘` 起送 7 天窗（`startTime` 與 `endTime` 都送）。追上之後改 `fromId`，cursor 仍是 `{mode: window, t}` 或 `{mode: fromId, id}`。

`fromId` 是否包含該筆、往新的還是往舊的，頁面沒寫。未確認。C7-03 在對拍之前不要假設 `+ 1`。

時間窗裡超過 `limit` 時，頁面沒寫會不會截斷。`fromId` 又不能和時間一起送，所以窗內無法用 `fromId` 翻頁。C7-03 在回滿 1000 筆時縮小時間窗再查。

回應範例的欄位有 `id`、`orderId`、`symbol`、`price`、`qty`、`quoteQty`、`side`、`positionSide`、`time`、`commission`、`commissionAsset`、`realizedPnl`。沒有 `clientOrderId`。cloid 要從訂單拿（E21-D011，§7）。

### 訂單

少量：`GET /fapi/v1/order`，weight 1（[Query Order](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade#query-order)）。`symbol` 必填，`orderId` 與 `origClientOrderId` 至少送一個。文件寫「orderId is self-increment for each specific symbol」。回應有 `clientOrderId`、`status`、`executedQty`、`avgPrice`、`cumQuote`、`origQty`、`price`、`side`、`positionSide`、`symbol`、`time`、`updateTime`。

大量：`GET /fapi/v1/allOrders`，weight 5（[All Orders](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/trade#all-orders)）。

- `limit` 預設 500，最大 1000。
- `orderId` 有送時，回 `orderId` 大於等於該值的單；否則回最近的單。
- 查詢時間窗 "must be less then 7 days( default as the recent 7 days)"。
- 2026-11-23 的參數表有 `symbol`，沒有把它標成必填。`orderId` 依 symbol 遞增，C7-04 仍然送 `symbol`。不送 `symbol` 的結果未確認。
- 只送 `orderId`、不送時間時，7 天預設還在不在，頁面沒寫。未確認。C7-04 的 `allOrders(orderId = 最小值)` 要先確認這點，否則 7 天前的單會不會出現還沒有依據。

### Funding 與手續費 `GET /fapi/v1/income`

[Get Income History](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account#get-income-history)。weight 30。帳號一條 stream（ARCHITECTURE §4.5），`incomeType=FUNDING_FEE`，預設每小時。

- `symbol` 可省略。
- `incomeType` 的列舉含 `FUNDING_FEE` 與 `COMMISSION`（同頁還有 `TRANSFER`、`WELCOME_BONUS`、`REALIZED_PNL`、`INSURANCE_CLEAR`、`REFERRAL_KICKBACK`、`COMMISSION_REBATE`，其餘列舉當天折疊，沒逐項抄）。
- `limit` 預設 100，最大 1000。
- `startTime`、`endTime` 都是含端點。兩個都不送時，回近 7 天。
- 參數表沒有 `page`。不要送 `page`。2023-08-14 的 changelog 曾加上 `page`；以讀取日的端點頁為準。
- 時間窗上限頁面沒寫。`-1127` 的訊息是「More than %s hours between startTime and endTime.」，小時數未確認。
- 回應欄位：`symbol`、`incomeType`、`income`、`asset`、`info`、`time`、`tranId`、`tradeId`。

`COMMISSION` 的 `symbol` 給 C7-06 告警用：出現在 income、卻不在落庫清單的商品只告警，不建 stream（C7-D001）。

## 4. 查詢期限

| 資料 | 期限 | 過期之後 | 出處 |
|---|---|---|---|
| 成交 `userTrades` | 近 3 個月 | 未確認（頁面只寫 Only support querying trade in the past 3 months） | 端點頁 |
| 訂單 `GET /fapi/v1/order`、`allOrders` | 建立時間 + 90 天之後找不到；另外，狀態是 `CANCELED` 或 `EXPIRED`、沒有成交、而且建立時間 + 3 天已過，也找不到 | 未確認是空結果還是錯誤碼。頁面只寫 These orders will not be found | 兩支端點頁的同一段 |
| income | 近 3 個月（Income history only contains data for the last three months.） | 未確認 | income 端點頁 |

訂單這段是在 UM 的 `GET /fapi/v1/order` 與 `allOrders` 上讀到的。ARCHITECTURE §4.5 原先引的是 COIN-M 的例子；UM 端點頁的 90 天與此相同。

成交的 3 個月以端點頁為準。[changelog](https://developers.binance.com/docs/derivatives) 2024-10-21 曾寫 `userTrades` 自 2024-10-30 起只留 6 個月。讀取日的端點頁寫 3 個月，不採用 6 個月。

`not_found` 的處理仍是 §4.5：標成 `unresolved` 並告警。要走到這個類別，`-2013` 得先被 `refine` 對上（§6）。

## 5. 限速

[General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)：

- request weight 以 IP 計，不以 API key 計。「The limits on the API are based on the IPs, not the API keys.」
- 每筆回應帶 `X-MBX-USED-WEIGHT-(intervalNum)(intervalLetter)`，是這個 IP 已用的 weight。
- 超限回 HTTP 429。「When a 429 is received, it's your obligation as an API to back off」。對到 `rate_limited`。
- 收到 429 之後繼續打，會被自動封 IP，HTTP 418。對到 `ip_banned`。再犯的封禁時間從 2 分鐘到 3 天。
- 下單次數是另一個限制，以帳號計，header 是 `X-MBX-ORDER-COUNT-(intervalNum)(intervalLetter)`。這份的端點都是讀取，不占下單次數。`-1015` 仍可能出現，對到 `rate_limited`（§6）。
- `Retry-After` 這幾頁都沒寫。未確認。worker 有這個 header 時照 ARCHITECTURE §4.4 用；沒有時走既有的退避。

每分鐘的 `REQUEST_WEIGHT` 數字，一般資訊只寫在 `GET /fapi/v1/exchangeInfo` 的 `rateLimits`。這份沒有打那支。common-definition 的範例物件寫 `REQUEST_WEIGHT`／`MINUTE`／`limit: 2400`，同一段也有 `ORDERS` 的 `1200`。那是格式範例，不當成上限。上限未確認。

現行程式 `WeightLimit = 6000`、`WeightBudget = 4800` 是現貨的每分鐘預算，`noteWeight` 對 fapi 用同一組。C6-03 把 fapi 的預算分開，不要等用到 4800 才停。分開之後的數字仍等 `exchangeInfo` 的文件或另一次讀取，不在這份填。

這份用到的 weight：

| 端點 | weight |
|---|---|
| `GET /fapi/v3/balance` | 5 |
| `GET /fapi/v3/positionRisk` | 5 |
| `GET /fapi/v2/positionRisk` | 5（快照不打） |
| `GET /fapi/v1/userTrades` | 5 |
| `GET /fapi/v1/allOrders` | 5 |
| `GET /fapi/v1/order` | 1 |
| `GET /fapi/v1/income` | 30 |
| `GET /fapi/v1/positionSide/dual` | 30（快照不打） |

[changelog](https://developers.binance.com/docs/derivatives) 2024-08-07 曾寫 `GET /fapi/v2/balance`、`/fapi/v2/account`、`/fapi/v2/positionRisk` 的 weight 自 2024-09-03 起由 5 改為 10。讀取日的 v2 `positionRisk` 頁寫 5。快照打的是 v3，weight 用 v3 頁上的 5。`GET /fapi/v2/account` 這次沒有打開，weight 未確認。

income 的 30 乘上 job 的請求預算會很大，而 IP 上限未確認。C7-05 不要在上限寫進這份之前把 20 次請求都打滿。

## 6. 錯誤碼

HTTP 的預設在 [General Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/general-info)。錯誤碼的文字在 [Error Codes](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/error-code)。錯誤碼頁沒有寫 HTTP status。

`refine`（`internal/venue/binance`）現在只改四個碼：`-1003`、`-1015` → `rate_limited`，`-2014`、`-2015` → `auth`。HTTP 418 在 `refine` 之前就返回，維持 `ip_banned`。其餘沿 `classify.FromHTTP`。

| 來源 | 文件 | 目標類別 | 與 `refine` |
|---|---|---|---|
| HTTP 401 | 一般資訊沒有單獨定義。`-2015` 常走這條 | `auth` | 一致（status 或碼） |
| HTTP 403 | 「HTTP 403 return code is used when the WAF Limit has been violated.」 | 見下方。目標不是 `auth` | 不一致 |
| HTTP 429 | 打破 rate limit | `rate_limited` | 一致 |
| HTTP 418 | 429 之後繼續打，IP 被封 | `ip_banned` | 一致 |
| HTTP 408 | 後端等候逾時 | `unavailable` | 一致 |
| HTTP 5xx | Binance 側錯誤。503 的 `-1008` 文件寫系統過載，重試，且不計入 rate limit | `unavailable` | 一致（503 走 5xx） |
| `-1003` TOO_MANY_REQUESTS | 請求過多；另一句訊息是 IP banned until | 418 維持 `ip_banned`，其餘 `rate_limited` | 一致 |
| `-1015` TOO_MANY_ORDERS | Too many new orders | `rate_limited` | 一致 |
| `-2014` BAD_API_KEY_FMT | API-key format invalid | `auth` | 一致 |
| `-2015` REJECTED_MBX_KEY | Invalid API-key, IP, or permissions for action | `auth` | 一致 |
| `-2013` NO_SUCH_ORDER | Order does not exist | `not_found` | 不一致，C7-04 |
| `-1022` INVALID_SIGNATURE | Signature for this request is not valid | `auth` | 不一致，C6-03 |
| `-1021` INVALID_TIMESTAMP | timestamp 在 `recvWindow` 外，或超前伺服器 1000ms | `bad_request` | 一致（HTTP 400 的預設） |
| `-1127` | startTime 與 endTime 相距超過若干小時 | `bad_request` | 一致。小時數未確認 |
| 其他碼 | 頁面有碼，這份沒逐條改類別 | HTTP status 的預設 | 一致 |

`auth` 的後果是 C6-D001：`account:<id>` 上任何 job 的 `auth` 都計入 credential，連續 2 次轉 `auth_failed`。`global:*` 只記錄（C4-D008）。`credential.verify` 時某個 kind 讀不到，仍只記在 kind 上（C5-D003）。計數的程式在 C6-06。在那之前，`internal/credential.ApplyAuth` 只計 `credential.verify` 與 `credential.probe`。

不一致的三處：

1. **`-2013`。** ARCHITECTURE §4.4 要 venue client 標成 `not_found`。`refine` 沒有這個 case。若 HTTP 是 400，現況是 `bad_request`，stream 會被暫停，`order.resolve` 不會走到 `unresolved`。C7-04 在 `refine` 加上 `-2013` → `not_found`。
2. **`-1022`。** 簽章無效應計入 credential，與 `-2014`、`-2015` 同一類。現況 HTTP 400 是 `bad_request`，會把 stream 暫停。C6-03 在 `refine` 加上 `-1022` → `auth`。
3. **HTTP 403。** 文件是 WAF，不是 key 無效。`classify.classOf` 與 ARCHITECTURE §4.4 的例子把 403 當 `auth`。WAF 若計入 C6-D001，會把 credential 打成 `auth_failed`。目標類別是 `rate_limited`（退避、不計 credential、不暫停成 bug）。C6-03 只在 Binance client 改這條，並改 ARCHITECTURE §4.4 的 403 例子。mftiko 的 403 → `auth`（C4-D008）不動。

## 7. 訂單終態

文件的 `status`（[Public Endpoints Info](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/common-definition)）：`NEW`、`PARTIALLY_FILLED`、`FILLED`、`CANCELED`、`REJECTED`、`EXPIRED`、`EXPIRED_IN_MATCH`。

寫入 `ledger.order` 的已結束，照 ARCHITECTURE §4.5：

| 狀態 | 已結束 |
|---|---|
| `FILLED` | 是 |
| `CANCELED`、`EXPIRED`、`EXPIRED_IN_MATCH`，且 `executedQty` > 0 | 是 |
| 上面三個狀態，`executedQty` 為 0 | 交易所側已結束，不寫 `ledger.order` |
| `PARTIALLY_FILLED` | 否。留在 `pending_order` |
| `NEW` | 否 |
| `REJECTED` | 未確認。枚舉有這個狀態，頁面沒寫它能否帶成交。沒有成交的不寫。有成交時 C7-04 再決定，先留在 pending |

`executedQty` 用回應裡的字串比較，大於 0 才算有成交。UM 的枚舉含 `EXPIRED_IN_MATCH`，有成交的與 §4.5 一樣算已結束。

沒有成交的 `CANCELED`／`EXPIRED` 在建立 3 天後從查詢端點消失（§4）。那些單本來就不寫入。

## 8. 去重鍵

範圍都在同一個 `exchange_account` 內。

| 資料 | 鍵 | 依據 |
|---|---|---|
| 成交 | `(symbol, id)` | 端點以 symbol 為單位，成交 id 是 `id`。文件沒寫 `id` 是否跨 symbol 唯一 |
| 訂單 | `(symbol, orderId)` | 「orderId is self-increment for each specific symbol」 |
| income | `(incomeType, tranId)` | 頁面寫「trandId is unique in the same incomeType for a user」。拼字是 `trandId`，回應欄位是 `tranId`。同一使用者、同一 `incomeType` 內唯一 |

`pending_order` 現在的鍵是 `(exchange_account_id, venue, native_order_id)`，沒有 symbol。`orderId` 依 symbol 遞增，C7-01／C7-04 的 native id 要帶 symbol，或把鍵改成含 symbol。只存 `orderId` 會在不同 symbol 上碰撞。

成交的 `orderId` 用來寫 `pending_order`，不是成交的去重鍵。`clientOrderId` 只在訂單回應裡。

## 9. 單位與商品

`userTrades` 的範例是 `qty` `0.002`、`price` `7819.01`、`quoteQty` `15.63802`（`BTCUSDT`）。頁面沒有寫 qty 的計價資產，也沒有把這個乘法寫成通則。數量存 `qty`，價格存 `price`，不換算。`baseQty` 出現在範例裡，沒有說明，不用它。

持倉的 `positionAmt`、`entryPrice`、`markPrice` 同樣原樣存字串。

商品反查（C4-02、C4-D011）：`(venue, category, exch_ticker)` → `mirror.symbol` 的 UID，活躍列優先。

- `venue` 固定 `BinanceUM`。
- `exch_ticker` 是端點的 `symbol`（範例 `BTCUSDT`）。
- `category` 這些帳號端點都沒有。用 SYM 寫進 `mirror.symbol` 的值（C4-D010）。對不到就不編 UID。

交割合約的 category、以及 `pair` 與 `symbol` 何時不同，這幾頁沒寫。未確認。

## 與現行程式不一致

| 項目 | 現況 | 改哪裡 |
|---|---|---|
| `-1003`、`-1015`、`-2014`、`-2015`、HTTP 418／429 | 與本檔目標類別一致 | 不改 |
| `-2013` → `not_found` | `refine` 沒有這個 case | C7-04，`internal/venue/binance` 的 `refine` |
| `-1022` → `auth` | HTTP 400 維持 `bad_request` | C6-03，`refine` |
| HTTP 403 是 WAF，目標 `rate_limited` | `classify` 與 ARCHITECTURE §4.4 當 `auth` | C6-03，只改 Binance 這條，並改 §4.4 的例子 |
| C6-D001 任何 `account:<id>` job 的 `auth` 都計數 | `ApplyAuth` 只計 verify 與 probe | C6-06 |
| fapi 的 weight 預算 | 程式用現貨的 6000／4800；fapi 上限未確認 | C6-03 分開預算，數字仍待文件 |
| `orderId` 依 symbol 唯一 | `pending_order` 的鍵沒有 symbol | C7-01／C7-04 |

C6-03、C7-03～C7-05 加端點時照第 2、3 節，不在這張票加 client。
