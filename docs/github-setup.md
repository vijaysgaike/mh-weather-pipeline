# Create the GitHub repository after M1

Git is initialized locally; GitHub CLI (`gh`) is not installed here. Create an
empty repository named **mh-weather-pipeline** at https://github.com/new.
Do not initialize it with a README, license, or .gitignore.

Then run these commands from this project (enter your GitHub username when asked):

```bash
read -r -p 'GitHub username: ' GITHUB_USERNAME
git remote add origin "https://github.com/${GITHUB_USERNAME}/mh-weather-pipeline.git"
git push -u origin main
```

Authenticate using your normal GitHub Git credentials. Do not store tokens in
files or remote URLs. No API secret is needed for Open-Meteo. No push has been
performed by this project setup. CI badges can be linked once the repository
exists; see README.md.
