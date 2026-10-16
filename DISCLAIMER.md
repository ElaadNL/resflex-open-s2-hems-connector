<!--
SPDX-FileCopyrightText: ElaadNL

SPDX-License-Identifier: Apache-2.0
-->

# Disclaimer

## Purpose and nature of this software

This software has been developed under the coordination of ElaadNL, in collaboration
with market parties, as part of the
[Residential Flexibility](https://elaad.nl/en/projects/residential-flexibility/)
initiative to accelerate interoperability and residential energy flexibility. It is
made available to the market on a best-effort basis as an open reference
implementation and building block. It is not a finished, certified, or end-user
product, and it should be used and assessed as such. More information about the
project, its background, and upcoming test events can be found on the
[project page](https://elaad.nl/en/projects/residential-flexibility/).

## Security review

Prior to publication, the source code in this repository was subjected to an
independent security review by the European Network for Cyber Security (ENCS). Based
on that review, the code, in the state it was in at the time of review, was
considered to be of an acceptable security level.

Cybersecurity is, however, by definition a snapshot in time. New vulnerabilities may
be discovered at any moment in this code, in the protocols and cryptographic
mechanisms it implements, or in the third-party libraries it depends on. Although
the use of external dependencies has been deliberately kept to a minimum, this
software does rely on third-party components over which ElaadNL has no control. The
security review does not constitute a guarantee, certification, or warranty that
this software is or will remain free of vulnerabilities.

## Responsibility of implementers

Any party that integrates, deploys, or distributes this software, including in
whole or in part, in a product, service, or system (the "Implementer") does so
entirely at its own risk and under its own responsibility. The Implementer is solely
responsible for:

- the secure integration, configuration, and operation of the software within its
  product, including the secure handling of pairing, credentials, and
  cryptographic keys;
- maintaining appropriate attention to cybersecurity throughout its development
  lifecycle, including monitoring and updating dependencies;
- having the resulting product tested and assessed for security on a regular and
  ongoing basis; and
- ensuring compliance with all laws, regulations, and standards applicable to its
  product and market.

## Standards, trademarks, and certification

Names of protocols, standards, organisations, and products in this repository are
used only to identify the technologies and devices this software implements or
interoperates with. They may be trademarks of their respective owners. Their use
does not imply any affiliation with, or endorsement by, those owners.

ElaadNL does not certify products. Conformance testing and certification of a
product that incorporates this software is the sole responsibility of the
manufacturer of that product and must be carried out on the end product itself.

**S2.** This software implements parts of the S2 standard (EN 50491-12-2) and the
S2 Connect specification. It has not been certified. Its use does not by itself
make a product S2-compliant, nor does it entitle anyone to claim S2 compliance or
certification. Manufacturers who want to make such claims must have their end
product assessed through the applicable S2 certification programme.

## No warranty and limitation of liability

This software is provided "AS IS", without warranties or conditions of any kind,
whether express or implied, including but not limited to warranties of
merchantability, fitness for a particular purpose, security, accuracy, or
non-infringement.

To the maximum extent permitted by applicable law, ElaadNL, its partners, and the
contributors to this repository shall not be liable for any direct, indirect,
incidental, special, exemplary, or consequential damages (including but not limited
to loss of data, loss of profits, business interruption, damage to equipment, or
damage resulting from security incidents) arising in any way out of the use of, or
inability to use, this software, even if advised of the possibility of such damage.

This disclaimer supplements, and does not limit, the disclaimer of warranty and
limitation of liability set out in Sections 7 and 8 of the Apache License 2.0.

## License

This software is licensed under the Apache License, Version 2.0. Subject to the terms
of that license, anyone is free to use, modify, and distribute this software,
including for commercial purposes. See the [LICENSE](LICENSE) file for the full
terms.

## Acknowledgement and contributions

This software was created to give the market a shared foundation for residential
flexibility and should be used in that spirit. Although not required by the license,
we greatly appreciate it if parties using this software let us know, and, where
possible, contribute improvements back to the project. See
[CONTRIBUTING.md](CONTRIBUTING.md) for how to contribute. Suspected security
vulnerabilities should be reported responsibly as described in
[SECURITY.md](SECURITY.md), not through public issues.
