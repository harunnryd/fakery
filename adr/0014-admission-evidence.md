# Admission evidence

Status: accepted

The Meet leave control appears before host admission, so its presence cannot
prove that a guest entered the call. The join recipe now treats the guest
waiting-screen phrases `Asking to be let in`, `Please wait until a meeting
host brings you into the call`, `let you in`, and their Indonesian or English
waiting variants as negative evidence. It only marks a run admitted after
those signals disappear. This keeps `joined_at` aligned with the host-
controlled transition instead of the knock transition.
