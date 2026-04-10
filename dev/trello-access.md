# Trello Access

## Credentials

Two environment variables are available in the shell:

- `TRELLO_API_KEY`
- `TRELLO_TOKEN`

Append `key=$TRELLO_API_KEY&token=$TRELLO_TOKEN` to every API call.

## My identity

- **Member ID:** `51ddbea9677856fc08002c78`
- **Username:** `paolocesarecalvi`

## Primary board: Luthien

- **Board ID:** `67cf59bdf2e5e435dcfc5690`
- **URL:** https://trello.com/b/ehoxykPf/luthien

### Lists

| Name | ID |
|---|---|
| Top Priority | `67cf5d21e1e116388268ecd3` |
| This Sprint | `67cf59bdf2e5e435dcfc568a` |
| In Progress (Today) | `67cf5d3518d519261ba9de2a` |
| Next Sprint (4/6) | `69c45e08412ac658d32c12a7` |
| DONE!! Yay! | `69cd6eeec0c8b6cae04a3d8a` |
| uncategorized | `69cd9dbd3ade0154ab83a022` |

## Common operations

```bash
# List all boards
curl -s "https://api.trello.com/1/members/me/boards?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN&fields=name,id" | jq .

# List cards in a list
curl -s "https://api.trello.com/1/lists/{listId}/cards?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN&fields=name,id,idMembers,due,desc" | jq .

# Get my open cards (all boards)
curl -s "https://api.trello.com/1/members/me/cards?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN&filter=open&fields=name,idList,url,due" | jq .

# Get a single card
curl -s "https://api.trello.com/1/cards/{cardId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" | jq .

# Move a card to a different list
curl -s -X PUT "https://api.trello.com/1/cards/{cardId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "idList={newListId}"

# Assign myself to a card
curl -s -X POST "https://api.trello.com/1/cards/{cardId}/idMembers?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "value=51ddbea9677856fc08002c78"

# Add a comment to a card
curl -s -X POST "https://api.trello.com/1/cards/{cardId}/actions/comments?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "text=Your comment here"

# Create a card
curl -s -X POST "https://api.trello.com/1/cards?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "idList={listId}&name=Card Title&desc=Description"

# Archive (close) a card
curl -s -X PUT "https://api.trello.com/1/cards/{cardId}?key=$TRELLO_API_KEY&token=$TRELLO_TOKEN" \
  -d "closed=true"
```

## Workflow conventions (Luthien project)

- **Starting work:** assign yourself + move card to "In Progress (Today)"
- **Done:** move card to "DONE!! Yay!"
- Card IDs in URLs use a short slug (`https://trello.com/c/{shortSlug}/...`) — always use the full 24-char hex `id` field for API calls
- One PR = one card; reference the card in the PR description
