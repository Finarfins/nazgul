$env:HTTPS_PROXY = ''
$env:HTTP_PROXY = ''
$env:ALL_PROXY = ''

$commitMessage = @"
RC hardening: fix reports lint and Windows backup reliability (#65)
"@

gh pr merge 65 --squash --delete-branch --message $commitMessage
