# Python subprocess execution with injected functions and restricted capabilities — how to safely exec arbitrary Python code with a limited namespace, pass data in and out, intercept function calls, and communicate results back from a child process to a parent process

## Summary
Research into 'Python subprocess execution with injected functions and restricted capabilities — how to safely exec arbitrary Python code with a limited namespace, pass data in and out, intercept function calls, and communicate results back from a child process to a parent process' (5 sources, 14 facts).

## Key Findings
- Python is an interpreted, interactive, object-oriented, open-source programming language.  [sources: Python, python]
- Python combines remarkable power with very clear syntax.  [sources: python]
- Some additional license information which was able to be auto-detected might be found in the repo-info repository's python/ directory ⁠ .  [sources: python]
- Back Back python Docker Official Image • 1B+ • 10K+ Python is an interpreted, interactive, object-oriented, open-source programming language.  [sources: python]
- Unless you are working in an environment where only the python image will be deployed and you have space constraints, we highly recommend using the default image of this repository.  [sources: python]
- CMD [ "python", "./your-daemon-or-script.py" ] Copy or (if you need to use Python 2): FROM python:2 WORKDIR /usr/src/app COPY requirements.txt ./ RUN pip install --no-cache-dir -r requirements.txt COPY . .  [sources: python]
- When using this image pip install will work if a suitable built distribution is available for the Python distribution package being installed. pip install may fail when installing a Python distribution package from a source distribution.  [sources: python]
- The majority of arbitrary pip install s should be successful without additional header/development Debian packages. ⁠ python:<version>-alpine This image is based on the popular Alpine Linux project ⁠ , available in the alpine official image ⁠ .  [sources: python]
- For information about how to get Docker running on Windows, please see the relevant "Quick Start" guide provided by Microsoft: Windows Containers Quick Start ⁠ ⁠ License View license information for Python 2 ⁠ and Python 3 ⁠ .  [sources: python]
- Using this image as a base, add the things you need in your own Dockerfile (see the alpine image description ⁠ for examples of how to install packages if you are unfamiliar). ⁠ python:<version>-windowsservercore This image is based on Windows Server Core ( mcr.microsoft.com/windows/servercore ) ⁠ .  [sources: python]
- CMD [ "python", "./your-daemon-or-script.py" ] Copy You can then build and run the Docker image: $ docker build -t my-python-app . $ docker run -it --rm --name my-running-app my-python-app Copy ⁠ Run a single Python script For many simple, single file projects, you may find it inconvenient to write a complete Dockerfile .  [sources: python]
- This reduces the number of packages that images that derive from it need to install, thus reducing the overall size of all images on your system. ⁠ python:<version>-slim This image does not contain the common Debian packages contained in the default tag and only contains the minimal Debian packages needed to run python .  [sources: python]
- This is an unfortunate side-effect of using the buildpack-deps image in the non-slim variants (and many distribution-provided tools being written against and likely to break with a different Python installation, so we can't safely remove/overwrite it). ⁠ Image Variants The python images come in many flavors, each designed for a specific use case. ⁠ python:<version> This is the defacto image.  [sources: python]

## Sources
- [Unify Amosclaud, secure autonomous execution, and add isolated cloud workspaces](https://github.com/wamakologeorge-dev/amosclaude-clean/pull/728) ([[learningMaterial/web/github-com-wamakologeorge-dev-amosclaude-clean-pull-728-e682cc85.html|archived]])
- [Python](https://hub.docker.com/r/dhi/python)
- [Eliminate Tk worker dispatch and bound scrcpy diagnostics](https://github.com/ahmetmelihafsar/macos-miracast-receiver/pull/20) ([[learningMaterial/web/github-com-ahmetmelihafsar-macos-miracast-receiver-pull-20-3957fcc2.html|archived]])
- [python](https://hub.docker.com/_/python) ([[learningMaterial/web/hub-docker-com-python-a6595cfd.html|archived]])
- [Security Analysis & Recommendations for PythonSCAD](https://github.com/pythonscad/pythonscad/issues/169) ([[learningMaterial/web/github-com-pythonscad-pythonscad-issues-169-dbdc24be.html|archived]])

## Follow-up Queries (gap fill)
- Python subprocess execution with injected functions and restricted capabilities — how to safely exec arbitrary Python code with a limited namespace, pass data in and out, intercept function calls, and communicate results back from a child process to a parent process communicate

<!-- research: 5 sources, 14 facts, 2 rounds -->

## Related

[[Procedure-Subprocess-Architecture]]
