# Prompt to paste into Lovable (league site)

Paste everything between the lines into the Lovable chat for salaryleaguehq.com.

---

Create a new backend (edge) function named `league-export` for an external, read-only data feed.

Requirements:
1. Method GET only. It must not write, update or delete anything.
2. Authentication: require the request header `x-export-token`. Compare it (constant-time) to a new
   secret named `LEAGUE_EXPORT_TOKEN`. If it is missing or wrong, return 401 with no data. This function
   does not use user login (disable JWT verification for this function only).
3. Query parameter `tables`: a comma-separated list. Only return tables on this allowlist; ignore any
   other name:
   players, team_lineups, team_custom_names, team_dead_money, team_draft_picks,
   contract_decision_submissions, ft_price_table, tag_periods, player_tags, team_tag_submissions,
   rfa_pool, rfa_auctions, rfa_bids, rfa_match_usage, rfa_contract_staging, rfa_tiebreak_rounds,
   rfa_tiebreak_bids, rookie_prospects, rookie_draft_results, trades, trade_approvals,
   traded_cap_space, transactions, league_settings, rule_book_versions, yahoo_team_map,
   yahoo_player_map, yahoo_matchups, yahoo_standings, yahoo_roster_snapshots, yahoo_roster_diffs,
   yahoo_player_stats, sync_runs
4. Never return these tables or any column holding emails, tokens, passwords or secrets:
   profiles, profiles_public, user_roles, invites, yahoo_oauth_tokens, yahoo_league_config,
   cron_job_secrets, email_send_log, email_send_state, email_unsubscribe_tokens, suppressed_emails,
   and all *_email_log tables. Also never return watchlist_items, draft_boards, trade_drafts or
   roster_scenarios (owners' private planning).
5. Read every row of each requested table (page through in batches of 1,000).
6. Response: JSON `{ "as_of": <ISO timestamp>, "tables": { "<table>": [rows...] } }`.
7. Generate a long random value for `LEAGUE_EXPORT_TOKEN` and store it as a secret. Show me the
   function's full URL and tell me where to copy the token from.

---

After Lovable creates it, add two secrets to the GitHub repo jdant19/dynasty-front-office
(Settings → Secrets and variables → Actions → New repository secret):
- `LEAGUE_EXPORT_URL` = the function's full URL
- `LEAGUE_EXPORT_TOKEN` = the token value
