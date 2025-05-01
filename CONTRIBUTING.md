# Contributing to CoMapeo SMP Plugin

Thank you for your interest in contributing to the CoMapeo SMP Plugin! This document provides guidelines and instructions for contributing.

## Development Setup

1. Clone the repository:
   ```
   git clone https://github.com/digidem/qgis-smp-plugin.git
   ```

2. Install the plugin in development mode:
   - Create a symbolic link from the repository to your QGIS plugins directory:
     - Linux: `ln -s /path/to/qgis-smp-plugin ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/comapeo_smp`
     - Windows: Use directory junction or copy the files
     - macOS: `ln -s /path/to/qgis-smp-plugin ~/Library/Application\ Support/QGIS/QGIS3/profiles/default/python/plugins/comapeo_smp`

3. Enable the plugin in QGIS:
   - Open QGIS
   - Go to `Plugins` > `Manage and Install Plugins...`
   - Find and enable "CoMapeo SMP" in the `Installed` tab

## Making Changes

1. Create a new branch for your changes:
   ```
   git checkout -b feature/your-feature-name
   ```

2. Make your changes to the code

3. Test your changes:
   ```
   make test
   ```

4. Update documentation if necessary

5. Commit your changes with a descriptive commit message:
   ```
   git commit -m "Add feature: description of your changes"
   ```

6. Push your branch to GitHub:
   ```
   git push origin feature/your-feature-name
   ```

7. Create a Pull Request on GitHub

## Code Style

- Follow PEP 8 style guidelines for Python code
- Use meaningful variable and function names
- Add comments for complex logic
- Write docstrings for functions and classes

## Testing

- Add tests for new features
- Make sure all tests pass before submitting a Pull Request
- Run tests using `make test`

## Versioning

We use semantic versioning (MAJOR.MINOR.PATCH):
- MAJOR version for incompatible API changes
- MINOR version for new functionality in a backward-compatible manner
- PATCH version for backward-compatible bug fixes

## Release Process

1. Update the version number in `metadata.txt`
2. Update the changelog in `metadata.txt` if applicable
3. Commit the changes
4. Push to the main branch
5. GitHub Actions will automatically create a new release

## Questions?

If you have any questions or need help, please open an issue on GitHub.
