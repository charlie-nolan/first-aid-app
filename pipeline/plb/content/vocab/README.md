# Vocabulary subsets for the speech test

`telling_1024.txt` / `telling_512.txt`: one word per line, ranked:
10 reserved words, then the life-threat pin words from topics.yaml (69 on 11-17), then the remaining
words by how much each tells about the topic (mutual information on the round-3 router training data).
The 512 list is the first 512 lines of the 1024 list. Ids stay those of lexicon.yaml (2048); these lists only
restrict the decoder (`fasr evaluate --vocab <list>`).

Text-only result (router, gemma test, top-1/top-6): 2048 = 58/85, 1024 telling = 58/83, 512 telling = 52/79.
Rebuilt 11-17 with the same ranking after more pins landed, so they differ slightly from the measured lists.
