#!/bin/bash
word=$(shuf -n 1 /usr/share/dict/words)
res=$(llcat -u "$1" -m "$2" -ns -nt "This is a test. Do not be conversational. Respond only with the word '$word'")
[[ "$word" == "$res" ]]
echo "$? $1 $2 (($word)) (($res))"
