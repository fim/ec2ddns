#!/usr/bin/env python

import re
import socket
import sys
import optparse
import boto

from boto.route53.record import ResourceRecordSets

def _host_absolute(name):
    """
    Ensure that hostname is absolute.
    Prevents us from making relative DNS records by mistake.
    """

    if not name.endswith("."):
        name = name + "."

    return name

def _domain_from_host(name):
    """
    Return domain from hostname.
    Cuts before first dot.
    """

    name = name.split(".")
    if not len(name) > 1:
        return False

    name = name[1:]
    name = ".".join(name)

    return name

def parse_opts():
    usage = "usage: %prog [options] fqdn target"
    parser = optparse.OptionParser(usage=usage)

    parser.add_option("-k", "--key", dest="key",
            default=boto.config.get("Credentials", "aws_access_key_id") or "",
            help="AWS access ID")
    parser.add_option("-s", "--secret", dest="secret",
            default=boto.config.get("Credentials", "aws_secret_access_key") or "",
            help="AWS secret key")
    parser.add_option("-t", "--ttl", dest="ttl", default="60")
    parser.add_option("-d", "--delete", dest="delete", action="store_true", default=False)
    parser.add_option("-v", "--verbose", dest="verbose", action="store_true", default=False)
    (options, args) = parser.parse_args()

    if (options.delete and len(args) != 1) or (not options.delete and len(args) != 2):
        parser.print_help()
        sys.exit(1)

    if not len(options.key) == 20 or not len(options.secret) == 40:
        print "Need a valid AWS key and secret"
        parser.print_help()
        sys.exit(1)

    return (options, args)

def main():
    options, args= parse_opts()

    hostname_desired = _host_absolute(args[0])
    domainname = _domain_from_host(hostname_desired)

    # Deletion replaces the record with a CNAME to unreg.domain
    # This reduces negative caching without modifying the zone's TTL and
    # gives some indication of machines that have registered and since gone.
    if options.delete:
        target = "unreg." + domainname
        rrtype = "CNAME"

    # New records are A records if target is IP, CNAME records if target is a
    # fqdn.
    else:
        target = args[1]
        try:
            socket.inet_aton(target)
        except socket.error:
            rrtype = "CNAME"
        else:
            rrtype = "A"

    if not domainname:
        print "Unable to determine domainname from hostname"
        sys.exit(1)

    # Connect to route53 with AWS credentials from CLI.
    r53 = boto.connect_route53(aws_access_key_id=options.key,
        aws_secret_access_key=options.secret)

    # Obtain hostedZone IDs that directly or indirectly parent our domainname.
    # Ugly manifestation from the current API/library.
    zones = [ hz
        for hz in r53.get_all_hosted_zones().ListHostedZonesResponse.HostedZones
        if re.search('\.?%s$' % re.escape(hz.Name), domainname) ]

    if not len(zones):
        print "No hostedZones found for %r" % domainname
        sys.exit(1)

    # Find the longest, most specific, zone.
    zone_id = max(zones, key=lambda x:len(x['Name']))['Id'].replace('/hostedzone/', '')

    # The API doesn't filter results. We can retrieve the required record
    # and everything that occurs after it lexicographically. We then have to
    # filter the desired entry out of the result set.
    rrs = [ rr for rr in r53.get_all_rrsets(zone_id, None, hostname_desired)
        if rr.name == hostname_desired ]

    # Dictionary representation of our desired record.
    rr_desired = {
        'alias_dns_name': None,
        'name': hostname_desired,
        'resource_records': [target],
        'ttl': options.ttl,
        'type': rrtype }

    rr_desired_exists = False
    transaction = ResourceRecordSets(r53, zone_id)

    # Remove any records that don't precisely match our desired CNAME.
    for rr in rrs:
        if rr.__dict__ == rr_desired:
            rr_desired_exists = True
        else:
            rr_change = transaction.add_change("DELETE", rr.name, rr.type)
            rr_change.resource_records = rr.resource_records
            rr_change.ttl = rr.ttl

    if not rr_desired_exists:
        rr_change = transaction.add_change("CREATE", hostname_desired, rrtype)
        rr_change.resource_records = [target]
        rr_change.ttl = options.ttl

    if transaction.changes:
        if options.verbose:
            print transaction.changes
        transaction.commit()

if __name__ == "__main__":
    main()
