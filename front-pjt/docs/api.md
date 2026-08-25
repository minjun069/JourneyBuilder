# API 데이터 흐름 검토

> **읽는 법**: 폼 key → 매퍼 변환 → 백엔드 필드 순서로 확인

---

## 인증

| 호출 위치 | 메서드 | 엔드포인트 | 요청 body |
|---|---|---|---|
| `stores/auth.js` | POST | `/auth/login/` | `{ email, password }` |
| `stores/auth.js` | POST | `/auth/register/` | payload 그대로 |
| `stores/auth.js` | POST | `/auth/logout/` | `{ refresh }` |
| `api/index.js` 인터셉터 | POST | `/auth/token/refresh/` | `{ refresh }` |

---

## 세션 / 메시지

| 호출 위치 | 함수 | 메서드 | 엔드포인트 |
|---|---|---|---|
| `stores/ai.js` `initSession` | `getSessions` | GET | `/consultations/` |
| `stores/ai.js` `initSession` | `createSession` | POST | `/consultations/` |
| `stores/ai.js` `loadMessages` | `getMessages` | GET | `/consultations/{sid}/messages/` |
| `stores/ai.js` `sendMessage` | `postMessage` | POST | `/consultations/{sid}/messages/` → body: `{ content }` |

---

## 폼 데이터 PUT — 필드 매핑

### basic-info
`PUT /consultations/{sid}/data/basic-info/`

| 폼 key | → 백엔드 필드 | 변환 |
|---|---|---|
| `age` | `age` | `Number` |
| `job` | `job` | 그대로 |
| `marital` | `marital_status` | 그대로 |
| `dependents` | `dependents` | 객체 그대로 |
| `tendency` | `tendency` | 배열 그대로 |
| `interest` | `interest` | 그대로 |
| `need` | `need` | 그대로 |
| `earnings` | `monthly_income` | `Number` |
| `retirementAge` | `retirement_age` | `Number` |
| `endAge` | `end_age` | `Number` |

---

### financial-mindset
`PUT /consultations/{sid}/data/financial-mindset/`

| 폼 key | → 백엔드 필드 | 변환 |
|---|---|---|
| `values` | `values` | 그대로 |
| `pastExperience` | `past_experience` | 그대로 |
| `currentSituation` | `current_situation` | 그대로 |
| `currentChallenge` | `current_challenge` | 그대로 |
| `investPhilosophy` | `invest_philosophy` | 그대로 |
| `futureGoals` | `future_goals` | 그대로 |

---

### financial-satisfaction
`PUT /consultations/{sid}/data/financial-satisfaction/`

| 폼 key | → 백엔드 필드 | 변환 |
|---|---|---|
| `cashflow` | `cashflow_score` | 그대로 (1~5) |
| `riskManagement` | `risk_score` | 그대로 (1~5) |
| `investment` | `investment_score` | 그대로 (1~5) |
| `management` | `management_score` | 그대로 (1~5) |
| `inheritance` | `inheritance_score` | 그대로 (1~5) |

---

### financial-assets
`PUT /consultations/{sid}/data/financial-assets/` (배열)

| 폼 key | → 백엔드 필드 | 변환 |
|---|---|---|
| `assetType` | `asset_type` | 그대로 |
| `name` | `name` | 그대로 |
| `principal` | `principal` | `Number` |
| `value` | `current_value` | `Number` |
| `monthly` | `monthly_savings` | `Number` |
| `purpose` | `purpose` | 그대로 |

---

### pension-assets
`PUT /consultations/{sid}/data/pension-assets/` (배열)

| 폼 key | → 백엔드 필드 | 변환 |
|---|---|---|
| `type` | `type` | 그대로 |
| `name` | `name` | 그대로 |
| `value` | `current_value` | `Number` |
| `monthly` | `monthly_savings` | `Number` |
| `rate` | `expected_return_rate` | `Number` |
| `date` | `subscription_date` | 그대로 (date string) |
| `paymentPeriod` | `payment_period_years` | `Number` |
| `receiveMethod` | `receive_method` | 그대로 |
| `age` | `pension_start_age` | `Number` |
| `pensionPeriod` | `pension_period` | 그대로 |
| `expectedAmount` | `expected_amount` | `Number` |

---

### real-estate-assets
`PUT /consultations/{sid}/data/real-estate-assets/` (배열)

| 폼 key | → 백엔드 필드 | 변환 |
|---|---|---|
| `type` | `type` | 그대로 |
| `name` | `name` | 그대로 |
| `value` | `current_value` | `Number` |
| `purchase` | `acquisition_price` | `Number` |
| `date` | `acquisition_date` | 그대로 (date string) |
| `monthly` | `monthly_rental_income` | `Number` |
| `deposit` | `rental_deposit` | `Number` |
| `loan` | `secured_loan` | `Number` |

---

### liabilities
`PUT /consultations/{sid}/data/liabilities/` (배열)

| 폼 key | → 백엔드 필드 | 변환 |
|---|---|---|
| `type` | `type` | 그대로 |
| `name` | `name` | 그대로 |
| `value` | `balance` | `Number` |
| `rate` | `interest_rate` | `Number` |
| `rateType` | `rate_type` | 그대로 |
| `baseRate` | `base_rate` | 그대로 |
| `repayment` | `repayment_method` | 그대로 |
| `loan` | `loan_date` | 그대로 (date string) |
| `maturity` | `maturity_date` | 그대로 (date string) |
| `grace` | `grace_period_years` | `Number` |

---

### income
`PUT /consultations/{sid}/data/incomes/` (배열)

| 폼 key | → 백엔드 필드 | 변환 |
|---|---|---|
| `type` | `type` | 그대로 |
| `name` | `name` | 그대로 |
| `amount` | `amount` | `Number` |
| `fixed` | `is_fixed` | `'고정'` → `true` / `'변동'` → `false` |

---

### expenses
`PUT /consultations/{sid}/data/expenses/` (배열)

| 폼 key | → 백엔드 필드 | 변환 |
|---|---|---|
| `name` | `name` | 그대로 |
| `value` | `amount` | `Number` |
| `fixed` | `is_fixed` | `'고정'` → `true` / `'변동'` → `false` |
| `frequency` | `frequency` | 그대로 |

---

### financial-goals (2개 엔드포인트 동시 호출)

**goal-periods** `PUT /consultations/{sid}/data/goal-periods/`

| 폼 경로 | → 백엔드 필드 | 변환 |
|---|---|---|
| `form.before` / `form.after` | `phase` | `'before'` / `'after'` |
| `period.fromAge` | `from_age` | `Number` |
| `period.toAge` | `to_age` | `Number` |
| `item.name` | `items[].name` | 그대로 |
| `item.amount` | `items[].amount` | `Number` |
| `item.type` | `items[].is_fixed` | `'고정'` → `true` |

**goal-milestones** `PUT /consultations/{sid}/data/goal-milestones/`

| 폼 경로 | → 백엔드 필드 | 변환 |
|---|---|---|
| `form.before` / `form.after` | `phase` | `'before'` / `'after'` |
| `milestone.age` | `age` | `Number` |
| `milestone.category` | `category` | 그대로 |
| `milestone.content` | `content` | 그대로 |
| `milestone.amount` | `amount` | `Number` |
| `milestone.note` | `note` | 그대로 |

---

## 보고서

| 호출 위치 | 함수 | 메서드 | 엔드포인트 |
|---|---|---|---|
| `stores/ai.js` `fetchPresentationData` | `getReports` | GET | `/consultations/{sid}/reports/` |

---

## 커뮤니티

| 호출 위치 | 함수 | 메서드 | 엔드포인트 | 요청 body |
|---|---|---|---|---|
| `communityStore.js` `fetchPosts` | `getPosts` | GET | `/posts/` | — |
| `communityStore.js` `addPost` | `createPost` | POST | `/posts/` | `{ title, content, is_anonymous }` |
| — | `getPost` | GET | `/posts/{pk}/` | — |
| `communityStore.js` `editPost` | `updatePost` | PATCH | `/posts/{pk}/` | `{ title, content, is_anonymous }` |
| `communityStore.js` `removePost` | `deletePost` | DELETE | `/posts/{pk}/` | — |
| `communityStore.js` `toggleLike` | `toggleLike` | POST | `/posts/{pk}/like/` | — |